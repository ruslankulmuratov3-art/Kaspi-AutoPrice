from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.alert import Alert, AlertType
from app.models.autopilot import AutopilotJob, AutopilotJobItem, AutopilotJobStatus
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.competitor_service import competitor_service
from app.services.price_change_limiter import price_change_limiter
from app.services.pricing_engine import PricingDecision, pricing_engine
from app.services.xml_feed_service import XmlFeedError, xml_feed_service

logger = get_logger(__name__)


class AutoPilotService:
    """Persistent database-backed XML autopilot.

    The web process only claims jobs from PostgreSQL. Job progress, stop requests and resume
    cursor survive a Render restart. XML is activated only after a full catalog validation.
    """

    def __init__(self) -> None:
        self._worker_task: asyncio.Task | None = None
        self._started = False
        self._wake = asyncio.Event()
        self._local_store_locks: dict[int, asyncio.Lock] = {}

    def enabled(self) -> bool:
        return bool(settings.KASPI_AUTOPILOT_ENABLED)

    def _lock(self, store_id: int) -> asyncio.Lock:
        return self._local_store_locks.setdefault(int(store_id), asyncio.Lock())

    def start(self) -> None:
        if self._started or not self.enabled():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._started = True
        self._recover_stale_jobs()
        self._worker_task = loop.create_task(self._worker_loop())
        logger.info('Persistent autopilot worker started')

    async def stop(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._started = False

    def _recover_stale_jobs(self) -> None:
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=max(1, settings.KASPI_AUTOPILOT_HEARTBEAT_TIMEOUT_MINUTES))
            rows = db.query(AutopilotJob).filter(AutopilotJob.status == AutopilotJobStatus.RUNNING).all()
            for row in rows:
                if not row.heartbeat_at or row.heartbeat_at < cutoff:
                    row.status = AutopilotJobStatus.QUEUED
                    row.error_message = 'Задание восстановлено после перезапуска.'
                    db.add(row)
            db.commit()
        finally:
            db.close()

    def enqueue(self, db: Session, store_id: int, *, mode: str = 'all', query_filter: str = '', requested_limit: int = 0, warehouse_id: str = '') -> AutopilotJob:
        active = db.query(AutopilotJob).filter(AutopilotJob.store_id == int(store_id), AutopilotJob.status.in_([AutopilotJobStatus.QUEUED, AutopilotJobStatus.RUNNING, AutopilotJobStatus.PAUSED])).order_by(AutopilotJob.id.desc()).first()
        if active and active.status != AutopilotJobStatus.PAUSED:
            return active
        job = AutopilotJob(store_id=int(store_id), status=AutopilotJobStatus.QUEUED, mode=mode, query_filter=query_filter.strip(), requested_limit=max(0, int(requested_limit or 0)), warehouse_id=(warehouse_id or settings.KASPI_AUTOPILOT_WAREHOUSE_ID or 'PP1').strip())
        db.add(job)
        db.commit()
        db.refresh(job)
        self._wake.set()
        return job

    def request_stop(self, store_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.query(AutopilotJob).filter(AutopilotJob.store_id == int(store_id), AutopilotJob.status.in_([AutopilotJobStatus.QUEUED, AutopilotJobStatus.RUNNING])).order_by(AutopilotJob.id.desc()).first()
            if job:
                job.stop_requested = True
                db.add(job)
                db.commit()
        finally:
            db.close()
        self._wake.set()

    def resume(self, db: Session, job_id: int) -> AutopilotJob | None:
        job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
        if not job or job.status not in (AutopilotJobStatus.PAUSED, AutopilotJobStatus.ERROR, AutopilotJobStatus.CANCELLED):
            return job
        job.status = AutopilotJobStatus.QUEUED
        job.stop_requested = False
        job.error_message = ''
        job.finished_at = None
        db.add(job)
        db.commit()
        self._wake.set()
        return job

    def latest_job(self, db: Session, store_id: int | None) -> AutopilotJob | None:
        if not store_id:
            return None
        return db.query(AutopilotJob).filter(AutopilotJob.store_id == int(store_id)).order_by(AutopilotJob.id.desc()).first()

    def last_status(self, store_id: int | None) -> dict[str, Any] | None:
        if not store_id:
            return None
        db = SessionLocal()
        try:
            job = self.latest_job(db, int(store_id))
            if not job:
                return None
            percent = round(job.processed / max(1, job.total) * 100, 1) if job.total else 0
            return {
                'job_id': job.id,
                'running': job.status == AutopilotJobStatus.RUNNING,
                'status': job.status.value,
                'processed_now': job.processed,
                'total': job.total,
                'percent': percent,
                'changed': job.changed,
                'skipped': job.skipped + job.unchanged,
                'unchanged': job.unchanged,
                'queued': job.queued_changes,
                'errors': job.errors,
                'current_product_id': job.current_product_id,
                'started_at': job.started_at.isoformat(timespec='seconds') if job.started_at else None,
                'updated_at': job.updated_at.isoformat(timespec='seconds') if job.updated_at else None,
                'finished_at': job.finished_at.isoformat(timespec='seconds') if job.finished_at else None,
                'error': job.error_message,
            }
        finally:
            db.close()

    async def _worker_loop(self) -> None:
        await asyncio.sleep(max(1, int(settings.KASPI_AUTOPILOT_STARTUP_DELAY_SECONDS or 1)))
        next_schedule = datetime.utcnow()
        while True:
            try:
                if datetime.utcnow() >= next_schedule:
                    self._schedule_due_stores()
                    next_schedule = datetime.utcnow() + timedelta(minutes=max(5, int(settings.KASPI_AUTOPILOT_INTERVAL_MINUTES or 30)))
                job_id = self._claim_job()
                if job_id:
                    await self._execute_job(job_id)
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(1, int(settings.KASPI_AUTOPILOT_POLL_SECONDS or 3)))
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception('Autopilot worker loop failed: %s', exc)
                await asyncio.sleep(3)

    def _schedule_due_stores(self) -> None:
        if not self.enabled():
            return
        db = SessionLocal()
        try:
            stores = db.query(Store).filter(Store.is_active == True).all()
            cutoff = datetime.utcnow() - timedelta(minutes=max(5, int(settings.KASPI_AUTOPILOT_INTERVAL_MINUTES or 30)))
            for store in stores:
                active = db.query(AutopilotJob).filter(AutopilotJob.store_id == store.id, AutopilotJob.status.in_([AutopilotJobStatus.QUEUED, AutopilotJobStatus.RUNNING, AutopilotJobStatus.PAUSED])).first()
                recent = db.query(AutopilotJob).filter(AutopilotJob.store_id == store.id, AutopilotJob.created_at >= cutoff).first()
                if not active and not recent:
                    db.add(AutopilotJob(store_id=store.id, status=AutopilotJobStatus.QUEUED, mode='scheduled', warehouse_id=settings.KASPI_AUTOPILOT_WAREHOUSE_ID or 'PP1'))
            db.commit()
        finally:
            db.close()

    def _claim_job(self) -> int | None:
        db = SessionLocal()
        try:
            query = db.query(AutopilotJob).filter(AutopilotJob.status == AutopilotJobStatus.QUEUED).order_by(AutopilotJob.id.asc())
            try:
                query = query.with_for_update(skip_locked=True)
            except Exception:
                pass
            job = query.first()
            if not job:
                return None
            running = db.query(AutopilotJob).filter(AutopilotJob.store_id == job.store_id, AutopilotJob.status == AutopilotJobStatus.RUNNING, AutopilotJob.id != job.id).first()
            if running:
                return None
            job.status = AutopilotJobStatus.RUNNING
            job.started_at = job.started_at or datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            db.add(job)
            db.commit()
            return int(job.id)
        finally:
            db.close()

    def _product_ids(self, db: Session, job: AutopilotJob) -> list[int]:
        query = db.query(Product).filter(Product.store_id == job.store_id, Product.auto_pricing_enabled == True, Product.status == ProductStatus.ACTIVE, Product.min_price > 0, Product.max_price > 0)
        if settings.KASPI_AUTOPILOT_ONLY_IN_STOCK:
            query = query.filter(Product.stock != 0)
        if job.cursor_product_id:
            query = query.filter(Product.id > job.cursor_product_id)
        rows = query.order_by(Product.id.asc()).limit(max(1, min(job.requested_limit or settings.KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN, settings.KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN))).all()
        needle = ' '.join(str(job.query_filter or '').casefold().split())
        if needle:
            rows = [p for p in rows if needle in ' '.join([str(p.name or ''), str(p.kaspi_sku or ''), str(p.product_id or ''), str(p.brand or ''), str(p.model or '')]).casefold()]
        return [int(p.id) for p in rows]

    def _save_job_item(self, db: Session, job: AutopilotJob, item: dict[str, Any]) -> AutopilotJobItem:
        row = (
            db.query(AutopilotJobItem)
            .filter(AutopilotJobItem.job_id == int(job.id), AutopilotJobItem.product_id == int(item['product_id']))
            .first()
        )
        if not row:
            row = AutopilotJobItem(
                job_id=int(job.id),
                store_id=int(job.store_id),
                product_id=int(item['product_id']),
            )
        row.sku = str(item.get('sku') or '')[:255]
        row.product_name = str(item.get('name') or '')
        row.old_price = float(item.get('old_price') or 0)
        row.new_price = float(item.get('new_price') or row.old_price or 0)
        row.requested_price = float(item['requested_price']) if item.get('requested_price') is not None else None
        row.competitor_price = float(item['competitor_price']) if item.get('competitor_price') is not None else None
        row.changed = bool(item.get('changed'))
        row.status = str(item.get('status') or 'safe_skipped')[:40]
        row.reason = str(item.get('reason') or '')
        row.data_source = str(item.get('data_source') or '')[:80]
        row.cache_state = str(item.get('cache_state') or '')[:40]
        row.error_message = str(item.get('error_message') or '')[:2000]
        db.add(row)
        return row

    def _job_decisions(self, db: Session, job_id: int) -> list[dict[str, Any]]:
        rows = (
            db.query(AutopilotJobItem)
            .filter(AutopilotJobItem.job_id == int(job_id))
            .order_by(AutopilotJobItem.product_id.asc())
            .all()
        )
        return [
            {
                'product_id': int(row.product_id),
                'sku': row.sku,
                'name': row.product_name,
                'old_price': int(round(float(row.old_price or 0))),
                'new_price': int(round(float(row.new_price or row.old_price or 0))),
                'requested_price': int(round(float(row.requested_price))) if row.requested_price is not None else None,
                'competitor_price': float(row.competitor_price) if row.competitor_price is not None else None,
                'changed': bool(row.changed),
                'status': row.status,
                'reason': row.reason,
                'data_source': row.data_source,
                'cache_state': row.cache_state,
                'error_message': row.error_message,
            }
            for row in rows
        ]

    async def _execute_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
            if not job:
                return
            store_id = int(job.store_id)
        finally:
            db.close()
        async with self._lock(store_id):
            await self._run_job(job_id)

    async def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
            store = db.query(Store).filter(Store.id == job.store_id).first() if job else None
            if not job or not store:
                return
            product_ids = self._product_ids(db, job)
            job.total = max(int(job.total or 0), int(job.processed or 0) + len(product_ids))
            job.heartbeat_at = datetime.utcnow()
            db.add(job)
            db.commit()
        finally:
            db.close()

        for product_id in product_ids:
            db = SessionLocal()
            try:
                job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
                if not job:
                    return
                if job.stop_requested:
                    job.status = AutopilotJobStatus.PAUSED
                    job.finished_at = datetime.utcnow()
                    job.error_message = 'Остановлено пользователем. Можно продолжить.'
                    db.add(job)
                    db.commit()
                    return
                product = db.query(Product).filter(Product.id == int(product_id)).first()
                if not product:
                    item = {
                        'product_id': int(product_id),
                        'sku': '',
                        'name': '',
                        'old_price': 0,
                        'new_price': 0,
                        'changed': False,
                        'status': 'error',
                        'reason': 'Товар не найден во время обработки.',
                        'error_message': 'Товар не найден во время обработки.',
                        'data_source': '',
                        'cache_state': '',
                    }
                    self._save_job_item(db, job, item)
                    job.errors += 1
                    job.processed += 1
                    job.cursor_product_id = int(product_id)
                    db.add(job)
                    db.commit()
                    continue
                job.current_product_id = product.id
                job.cursor_product_id = product.id
                job.heartbeat_at = datetime.utcnow()
                db.add(job)
                db.commit()

                decision: PricingDecision = await pricing_engine.preview_product(db, product)
                item = {
                    'product_id': product.id,
                    'sku': product.kaspi_sku,
                    'name': product.name,
                    'old_price': int(round(float(product.current_price or 0))),
                    'new_price': int(round(float(decision.suggested_price or product.current_price or 0))),
                    'changed': bool(decision.can_apply and decision.suggested_price != decision.old_price),
                    'status': decision.status,
                    'reason': decision.reason,
                    'competitor_price': decision.competitor_price,
                    'data_source': decision.data_source,
                    'cache_state': decision.cache_state,
                }
                self._save_job_item(db, job, item)
                job.processed += 1
                if item['status'] == 'error':
                    job.errors += 1
                elif item['changed']:
                    job.changed += 1
                elif item['status'] == 'unchanged':
                    job.unchanged += 1
                else:
                    job.skipped += 1
                job.heartbeat_at = datetime.utcnow()
                db.add(job)
                db.commit()
            except Exception as exc:
                if 'job' in locals() and job:
                    product = db.query(Product).filter(Product.id == int(product_id)).first()
                    item = {
                        'product_id': int(product_id),
                        'sku': str(product.kaspi_sku or '') if product else '',
                        'name': str(product.name or '') if product else '',
                        'old_price': int(round(float(product.current_price or 0))) if product else 0,
                        'new_price': int(round(float(product.current_price or 0))) if product else 0,
                        'changed': False,
                        'status': 'error',
                        'reason': 'Ошибка обработки товара. Цена оставлена без изменений.',
                        'error_message': str(exc)[:2000],
                        'data_source': '',
                        'cache_state': '',
                    }
                    self._save_job_item(db, job, item)
                    job.errors += 1
                    job.processed += 1
                    job.cursor_product_id = int(product_id)
                    job.error_message = str(exc)[:500]
                    db.add(job)
                    db.commit()
                logger.exception('Autopilot product %s failed: %s', product_id, exc)
            finally:
                db.close()
            delay = max(0.0, float(settings.KASPI_AUTOPILOT_DELAY_SECONDS or 0))
            if delay:
                await asyncio.sleep(delay)

        db = SessionLocal()
        try:
            job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
            store = db.query(Store).filter(Store.id == job.store_id).first() if job else None
            if not job or not store:
                return
            decisions = self._job_decisions(db, int(job.id))
            full_products = xml_feed_service.expected_products(db, store.id)
            if not full_products:
                raise XmlFeedError('Нет активных товаров для полного XML.')
            product_by_id = {p.id: p for p in full_products}
            candidates = [item for item in decisions if item['changed'] and item['product_id'] in product_by_id]
            allowed, queued, budget = price_change_limiter.allocate(db, store.id, candidates)
            allowed_ids = {int(x['product_id']) for x in allowed}
            queued_ids = {int(x['product_id']) for x in queued}
            price_by_sku = {str(p.kaspi_sku): int(round(float(p.current_price or 0))) for p in full_products}
            for item in decisions:
                if int(item['product_id']) in allowed_ids:
                    price_by_sku[str(item['sku'])] = int(item['new_price'])
                    item['status'] = 'changed'
                elif int(item['product_id']) in queued_ids:
                    item['status'] = 'queued'
                    item['requested_price'] = item['new_price']
                    item['new_price'] = item['old_price']
                    item['changed'] = False
                    item['reason'] = 'В очереди: достигнут лимит изменений.'
            job.changed = len(allowed)
            job.queued_changes = len(queued)
            record = xml_feed_service.save_feed(store=store, products=full_products, price_by_sku=price_by_sku, warehouse_id=job.warehouse_id, processed=job.processed, changed=len(allowed), skipped=job.unchanged + job.skipped, queued=len(queued), errors=job.errors, q_filter=job.mode, details=decisions)
            if not record.get('is_active'):
                raise XmlFeedError(record.get('rejection_reason') or 'Новая XML-версия отклонена.')

            if settings.KASPI_AUTOPILOT_UPDATE_LOCAL_PRICE:
                for item in allowed:
                    product = product_by_id.get(int(item['product_id']))
                    if not product:
                        continue
                    old_price = float(product.current_price or 0)
                    new_price = float(item['new_price'])
                    product.current_price = new_price
                    db.add(product)
                    db.add(PriceHistory(product_id=product.id, old_price=old_price, new_price=new_price, reason=str(item.get('reason') or ''), source='xml_prepared'))
            price_change_limiter.record_applied(db, store.id, str(record['feed_id']), allowed)
            job.status = AutopilotJobStatus.DONE
            job.finished_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            job.current_product_id = None
            job.result_json = json.dumps({'feed_id': record['feed_id'], 'budget': budget, 'source_state': competitor_service.state_info(db)}, ensure_ascii=False)
            db.add(job)
            db.add(Alert(title='XML создан', body=f'{store.name}: {record["product_count"]} товаров, {len(allowed)} новых цен, {len(queued)} в очереди.', type=AlertType.SYSTEM))
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first()
            if job:
                job.status = AutopilotJobStatus.ERROR
                job.finished_at = datetime.utcnow()
                job.error_message = str(exc)[:1000]
                db.add(job)
                db.commit()
            logger.exception('Autopilot job %s failed: %s', job_id, exc)
        finally:
            db.close()

    async def rebuild_store_feed(self, db: Session, store_id: int, *, reason: str = 'manual', warehouse_id: str = '', limit_count: int = 0, q_filter: str = '', update_local_prices: bool | None = None) -> dict[str, Any]:
        job = self.enqueue(db, store_id, mode=reason, query_filter=q_filter, requested_limit=limit_count, warehouse_id=warehouse_id)
        return {'store_id': store_id, 'ok': True, 'queued': True, 'job_id': job.id, 'message': 'Задание поставлено в очередь.'}

    async def rebuild_all_stores(self, reason: str = 'manual') -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            results = []
            for store in db.query(Store).filter(Store.is_active == True).all():
                job = self.enqueue(db, store.id, mode=reason)
                results.append({'store_id': store.id, 'ok': True, 'job_id': job.id})
            return results
        finally:
            db.close()

    async def rebuild_store_if_stale(self, db: Session, store_id: int, reason: str = 'pull_if_stale') -> dict[str, Any] | None:
        # Deliberately never calculate inside a Kaspi XML request. It must return immediately.
        return None


autopilot_service = AutoPilotService()
