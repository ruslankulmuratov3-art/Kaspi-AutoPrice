from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.autopilot import AutopilotJob, AutopilotJobItem, AutopilotJobStatus, CompetitorSnapshot
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.competitor_service import competitor_service
from app.services.price_change_limiter import price_change_limiter
from app.services.pricing_engine import PricingDecision, pricing_engine
from app.services.xml_feed_service import XmlFeedError, xml_feed_service

logger = get_logger(__name__)


class IncrementalPricingService:
    """Apply one fresh competitor snapshot without rescanning the whole catalog."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._xml_tasks: dict[int, asyncio.Task] = {}

    def _lock(self, store_id: int) -> asyncio.Lock:
        return self._locks.setdefault(int(store_id), asyncio.Lock())

    def _active_job(self, db: Session, store_id: int) -> AutopilotJob:
        job = (
            db.query(AutopilotJob)
            .filter(
                AutopilotJob.store_id == int(store_id),
                AutopilotJob.mode == 'incremental',
                AutopilotJob.status.in_([AutopilotJobStatus.QUEUED, AutopilotJobStatus.RUNNING]),
            )
            .order_by(AutopilotJob.id.desc())
            .first()
        )
        if job:
            return job
        total = (
            db.query(Product)
            .filter(
                Product.store_id == int(store_id),
                Product.status == ProductStatus.ACTIVE,
                Product.auto_pricing_enabled == True,
                Product.min_price > 0,
                Product.max_price > 0,
            )
            .count()
        )
        job = AutopilotJob(
            store_id=int(store_id),
            status=AutopilotJobStatus.RUNNING,
            mode='incremental',
            total=total,
            started_at=datetime.utcnow(),
            heartbeat_at=datetime.utcnow(),
            worker_id='incremental-helper',
        )
        db.add(job)
        db.flush()
        return job

    @staticmethod
    def _item_payload(product: Product, decision: PricingDecision, *, source_device: str = '', http_status: int | None = None) -> dict[str, Any]:
        details = decision.details or {}
        rule = product.pricing_rule
        return {
            'product_id': int(product.id),
            'sku': str(product.kaspi_sku or ''),
            'name': str(product.name or ''),
            'old_price': int(round(float(product.current_price or 0))),
            'new_price': int(round(float(decision.suggested_price or product.current_price or 0))),
            'requested_price': int(round(float(decision.suggested_price))) if decision.suggested_price else None,
            'competitor_price': decision.competitor_price,
            'competitor_seller': str(details.get('competitor_seller') or ''),
            'competitor_seller_id': str(details.get('competitor_seller_id') or ''),
            'changed': bool(decision.can_apply and decision.suggested_price != decision.old_price),
            'status': decision.status,
            'reason': decision.reason,
            'data_source': decision.data_source,
            'cache_state': decision.cache_state,
            'error_message': decision.reason if decision.status == 'error' else '',
            'min_price': float(product.min_price or 0),
            'max_price': float(product.max_price or 0),
            'cost_price': float(product.cost_price or 0),
            'margin_percent': float(rule.min_margin_percent or 0) if rule else 0,
            'step': float(rule.beat_step or 0) if rule else 0,
            'http_status': http_status,
            'source_device': source_device,
        }

    def _upsert_item(self, db: Session, job: AutopilotJob, item: dict[str, Any]) -> AutopilotJobItem:
        row = (
            db.query(AutopilotJobItem)
            .filter(AutopilotJobItem.job_id == int(job.id), AutopilotJobItem.product_id == int(item['product_id']))
            .first()
        )
        if not row:
            row = AutopilotJobItem(job_id=int(job.id), store_id=int(job.store_id), product_id=int(item['product_id']))
        row.sku = item['sku']
        row.product_name = item['name']
        row.old_price = item['old_price']
        row.new_price = item['new_price']
        row.requested_price = item.get('requested_price')
        row.competitor_price = item.get('competitor_price')
        row.competitor_seller = item.get('competitor_seller', '')
        row.competitor_seller_id = item.get('competitor_seller_id', '')
        row.changed = bool(item.get('changed'))
        row.status = item.get('status', 'safe_skipped')
        row.reason = item.get('reason', '')
        row.data_source = item.get('data_source', '')
        row.cache_state = item.get('cache_state', '')
        row.error_message = item.get('error_message', '')
        row.min_price = item.get('min_price', 0)
        row.max_price = item.get('max_price', 0)
        row.cost_price = item.get('cost_price', 0)
        row.margin_percent = item.get('margin_percent', 0)
        row.step = item.get('step', 0)
        row.http_status = item.get('http_status')
        row.source_device = item.get('source_device', '')
        db.add(row)
        return row

    def _recount(self, db: Session, job: AutopilotJob) -> None:
        rows = db.query(AutopilotJobItem).filter(AutopilotJobItem.job_id == int(job.id)).all()
        job.processed = len(rows)
        job.changed = sum(1 for r in rows if r.status == 'changed' and r.changed)
        job.unchanged = sum(1 for r in rows if r.status == 'unchanged')
        job.errors = sum(1 for r in rows if r.status == 'error')
        job.queued_changes = sum(1 for r in rows if r.status == 'queued')
        job.skipped = max(0, len(rows) - job.changed - job.unchanged - job.errors - job.queued_changes)
        job.heartbeat_at = datetime.utcnow()
        job.status = AutopilotJobStatus.RUNNING
        db.add(job)

    async def process_product(self, product_id: int, *, source_device: str = '', http_status: int | None = None) -> dict[str, Any]:
        db = SessionLocal()
        try:
            product = db.query(Product).filter(Product.id == int(product_id)).first()
            if not product:
                return {'ok': False, 'status': 'error', 'reason': 'Товар не найден.'}
            store_id = int(product.store_id)
        finally:
            db.close()

        async with self._lock(store_id):
            db = SessionLocal()
            try:
                product = db.query(Product).filter(Product.id == int(product_id)).first()
                if not product:
                    return {'ok': False, 'status': 'error', 'reason': 'Товар не найден.'}
                snapshot = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.product_id == product.id).first()
                job = self._active_job(db, store_id)
                old_price = float(product.current_price or 0)
                if snapshot and snapshot.fetched_at:
                    offers = competitor_service._offers_from_snapshot(snapshot)
                    decision = pricing_engine.decide(
                        product,
                        offers,
                        product.pricing_rule,
                        source=snapshot.source or 'helper',
                        cache_state='fresh',
                    )
                else:
                    decision = PricingDecision(
                        product.id,
                        old_price,
                        old_price,
                        'Нет сохранённых данных конкурентов',
                        False,
                        'safe_skipped',
                        data_source='helper',
                        cache_state='missing',
                        details={'reason_code': 'missing_snapshot'},
                    )
                item = self._item_payload(product, decision, source_device=source_device, http_status=http_status)
                if item['changed']:
                    allowed, queued, _ = price_change_limiter.allocate(db, store_id, [item])
                    if allowed:
                        product.current_price = float(item['new_price'])
                        product.last_pricing_calculated_at = datetime.utcnow()
                        db.add(product)
                        db.add(PriceHistory(
                            product_id=product.id,
                            old_price=old_price,
                            new_price=float(item['new_price']),
                            reason=str(item['reason']),
                            source='helper_incremental',
                        ))
                        item['status'] = 'changed'
                    else:
                        item['status'] = 'queued'
                        item['changed'] = False
                        item['new_price'] = item['old_price']
                        item['reason'] = 'Изменение поставлено в безопасную очередь лимита.'
                else:
                    product.last_pricing_calculated_at = datetime.utcnow()
                    db.add(product)
                self._upsert_item(db, job, item)
                self._recount(db, job)
                db.commit()
                result = {'ok': True, 'job_id': int(job.id), **item}
            except Exception as exc:
                db.rollback()
                logger.exception('Incremental price calculation failed for %s: %s', product_id, exc)
                return {'ok': False, 'status': 'error', 'reason': str(exc)[:500]}
            finally:
                db.close()

        self.schedule_xml_rebuild(store_id)
        return result

    def schedule_xml_rebuild(self, store_id: int) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        previous = self._xml_tasks.get(int(store_id))
        if previous and not previous.done():
            previous.cancel()
        self._xml_tasks[int(store_id)] = loop.create_task(self._debounced_rebuild(int(store_id)))

    async def _debounced_rebuild(self, store_id: int) -> None:
        try:
            await asyncio.sleep(max(2, int(settings.HELPER_XML_DEBOUNCE_SECONDS or 20)))
            # Do not let the helper write a product while a full XML version is being
            # committed in local SQLite. PostgreSQL also gets a predictable ordering.
            async with self._lock(int(store_id)):
                await asyncio.to_thread(self.rebuild_xml_now, int(store_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception('Debounced XML rebuild failed for store %s: %s', store_id, exc)

    async def rebuild_xml_serialized(self, store_id: int, *, finish_job: bool = False) -> dict[str, Any]:
        async with self._lock(int(store_id)):
            return await asyncio.to_thread(self.rebuild_xml_now, int(store_id), finish_job=finish_job)

    def rebuild_xml_now(self, store_id: int, *, finish_job: bool = False) -> dict[str, Any]:
        db = SessionLocal()
        try:
            job = (
                db.query(AutopilotJob)
                .filter(AutopilotJob.store_id == int(store_id), AutopilotJob.mode == 'incremental')
                .order_by(AutopilotJob.id.desc())
                .first()
            )
            details: list[dict[str, Any]] = []
            if job:
                rows = db.query(AutopilotJobItem).filter(AutopilotJobItem.job_id == int(job.id)).order_by(AutopilotJobItem.product_id.asc()).all()
                details = [
                    {
                        'product_id': r.product_id,
                        'sku': r.sku,
                        'name': r.product_name,
                        'old_price': int(round(float(r.old_price or 0))),
                        'new_price': int(round(float(r.new_price or r.old_price or 0))),
                        'competitor_price': r.competitor_price,
                        'competitor_seller': r.competitor_seller,
                        'changed': r.changed,
                        'status': r.status,
                        'reason': r.reason,
                        'data_source': r.data_source,
                        'cache_state': r.cache_state,
                        'source_device': r.source_device,
                    }
                    for r in rows
                ]
                job_id = int(job.id)
            else:
                job_id = None
        finally:
            db.close()
        record = xml_feed_service.rebuild_current(store_id, source='helper_incremental', job_id=job_id, details=details)
        if job_id:
            db = SessionLocal()
            try:
                job = db.query(AutopilotJob).filter(AutopilotJob.id == job_id).first()
                if job:
                    for row in db.query(AutopilotJobItem).filter(AutopilotJobItem.job_id == job_id).all():
                        row.xml_feed_id = str(record.get('feed_id') or '')
                        db.add(row)
                    if finish_job:
                        job.status = AutopilotJobStatus.DONE
                        job.finished_at = datetime.utcnow()
                    job.heartbeat_at = datetime.utcnow()
                    db.add(job)
                    db.commit()
                    allowed = [x for x in details if x.get('changed')]
                    price_change_limiter.record_applied(db, store_id, str(record.get('feed_id') or ''), allowed)
                    db.commit()
            finally:
                db.close()
        return record


incremental_pricing_service = IncrementalPricingService()
