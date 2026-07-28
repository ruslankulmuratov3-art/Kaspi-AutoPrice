from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.alert import Alert, AlertType
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.pricing_engine import pricing_engine
from app.services.xml_feed_service import XmlFeedError, xml_feed_service

logger = get_logger(__name__)


class AutoPilotService:
    """Background XML autopilot.

    One ACTIVE.xlsx import saves products to DB. Afterwards the autopilot recalculates prices,
    rebuilds XML, saves XML history, and Kaspi pulls /kaspi-feed/{store_id}.xml.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._started = False
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_status: dict[int, dict[str, Any]] = {}
        self._stop_flags: set[int] = set()

    def enabled(self) -> bool:
        return bool(getattr(settings, 'KASPI_AUTOPILOT_ENABLED', True))

    def _lock_for(self, store_id: int) -> asyncio.Lock:
        if store_id not in self._locks:
            self._locks[store_id] = asyncio.Lock()
        return self._locks[store_id]

    def start(self) -> None:
        if self._started or not self.enabled():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning('Autopilot: event loop is not ready, background mode skipped')
            return
        self._started = True
        self._task = loop.create_task(self._run_loop())
        logger.info('Kaspi XML autopilot started')

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._started = False

    def request_stop(self, store_id: int) -> None:
        self._stop_flags.add(int(store_id))

    async def _run_loop(self) -> None:
        startup_delay = int(getattr(settings, 'KASPI_AUTOPILOT_STARTUP_DELAY_SECONDS', 25) or 25)
        interval_minutes = int(getattr(settings, 'KASPI_AUTOPILOT_INTERVAL_MINUTES', 60) or 60)
        interval_seconds = max(300, interval_minutes * 60)
        await asyncio.sleep(max(1, startup_delay))
        while True:
            try:
                await self.rebuild_all_stores(reason='background_loop')
            except Exception as exc:
                logger.exception('Autopilot loop failed: %s', exc)
            await asyncio.sleep(interval_seconds)

    def _parse_dt(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00').replace('+00:00', ''))
        except Exception:
            return None

    def is_feed_stale(self, store_id: int, stale_after_minutes: int | None = None) -> bool:
        record = xml_feed_service.get_record(store_id)
        if not record:
            return True
        updated_at = self._parse_dt(record.get('updated_at'))
        if not updated_at:
            return True
        stale_minutes = stale_after_minutes
        if stale_minutes is None:
            stale_minutes = int(getattr(settings, 'KASPI_XML_STALE_AFTER_MINUTES', 55) or 55)
        age_seconds = (datetime.utcnow() - updated_at).total_seconds()
        return age_seconds >= max(60, int(stale_minutes) * 60)

    async def rebuild_all_stores(self, reason: str = 'manual') -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            stores = db.query(Store).filter(Store.is_active == True).order_by(Store.id.asc()).all()
            results = []
            for store in stores:
                try:
                    results.append(await self.rebuild_store_feed(db, store.id, reason=reason))
                except Exception as exc:
                    logger.exception('Autopilot failed for store %s: %s', store.id, exc)
                    results.append({'store_id': store.id, 'ok': False, 'error': str(exc)[:300]})
            return results
        finally:
            db.close()

    async def rebuild_store_if_stale(self, db: Session, store_id: int, reason: str = 'pull_if_stale') -> dict[str, Any] | None:
        if not self.enabled() or not bool(getattr(settings, 'KASPI_XML_REBUILD_ON_PULL', True)):
            return None
        if not self.is_feed_stale(store_id):
            return None
        return await self.rebuild_store_feed(db, store_id, reason=reason)

    def _query_product_ids(self, db: Session, store_id: int, *, limit_count: int = 0, q_filter: str = '') -> list[int]:
        q = db.query(Product).filter(
            Product.store_id == store_id,
            Product.auto_pricing_enabled == True,
            Product.status == ProductStatus.ACTIVE,
            Product.min_price > 0,
            Product.max_price > 0,
        )
        if bool(getattr(settings, 'KASPI_AUTOPILOT_ONLY_IN_STOCK', True)):
            q = q.filter(Product.stock != 0)
        # pull a reasonable superset then use Python casefold for Cyrillic search reliability
        max_products = int(getattr(settings, 'KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN', 5000) or 5000)
        if limit_count and int(limit_count) > 0:
            max_products = min(max_products, int(limit_count))
        rows = q.order_by(Product.id.asc()).limit(max_products if not q_filter.strip() else 10000).all()
        if q_filter.strip():
            needle = ' '.join(q_filter.casefold().split())
            rows = [p for p in rows if needle in ' '.join([str(p.name or ''), str(p.kaspi_sku or ''), str(getattr(p, 'product_id', '') or ''), str(p.url or ''), str(p.brand or '')]).casefold()]
            rows = rows[:max_products]
        return [int(p.id) for p in rows]

    async def _process_one(self, product_id: int, update_local_prices: bool) -> dict[str, Any]:
        db = SessionLocal()
        try:
            product = db.query(Product).filter(Product.id == int(product_id)).first()
            if not product:
                return {'product_id': product_id, 'sku': '', 'name': '', 'old_price': 0, 'new_price': 0, 'delta': 0, 'changed': False, 'reason': 'Товар не найден', 'status': 'error'}
            old_price = int(round(float(product.current_price or 0)))
            new_price = old_price
            reason = 'Без изменения'
            status = 'same'
            try:
                decision = await pricing_engine.preview_product(db, product)
                reason = str(decision.reason or '')
                if decision.can_apply:
                    new_price = int(round(float(decision.suggested_price)))
                    status = 'changed' if new_price != old_price else 'same'
                else:
                    status = 'skipped'
            except Exception as exc:
                reason = f'Ошибка расчёта: {exc}'[:300]
                status = 'error'
                product.last_autopilot_error = reason
                db.add(product)
                db.commit()

            if new_price != old_price and update_local_prices:
                product.current_price = new_price
                product.last_autopilot_error = ''
                db.add(product)
                db.add(PriceHistory(product_id=product.id, old_price=old_price, new_price=new_price, reason=f'XML автопилот: {reason}', source='xml_autopilot_prepared'))
                db.commit()

            return {
                'product_id': product.id,
                'sku': str(product.kaspi_sku or ''),
                'name': str(product.name or product.kaspi_sku or ''),
                'old_price': old_price,
                'new_price': new_price,
                'delta': new_price - old_price,
                'changed': new_price != old_price,
                'reason': reason,
                'status': status,
                'url': product.url or '',
            }
        finally:
            db.close()

    async def rebuild_store_feed(
        self,
        db: Session,
        store_id: int,
        *,
        reason: str = 'manual',
        warehouse_id: str = '',
        limit_count: int = 0,
        q_filter: str = '',
        update_local_prices: bool | None = None,
    ) -> dict[str, Any]:
        store_id = int(store_id)
        lock = self._lock_for(store_id)
        if lock.locked():
            return {'store_id': store_id, 'ok': False, 'busy': True, 'message': 'Автопилот уже считает XML для этого магазина'}
        async with lock:
            self._stop_flags.discard(store_id)
            store = db.query(Store).filter(Store.id == store_id).first()
            if not store:
                raise XmlFeedError('Магазин не найден')
            product_ids = self._query_product_ids(db, store_id, limit_count=limit_count, q_filter=q_filter)
            if not product_ids:
                raise XmlFeedError('Нет готовых товаров для XML. Сначала импортируй ACTIVE.xlsx и примени лимиты.')
            if update_local_prices is None:
                update_local_prices = bool(getattr(settings, 'KASPI_AUTOPILOT_UPDATE_LOCAL_PRICE', True))
            warehouse_id = (warehouse_id or '').strip() or str(getattr(settings, 'KASPI_AUTOPILOT_WAREHOUSE_ID', '') or 'PP1')
            concurrency = max(1, min(int(getattr(settings, 'KASPI_AUTOPILOT_CONCURRENCY', 3) or 3), 5))
            semaphore = asyncio.Semaphore(concurrency)
            total = len(product_ids)
            processed = 0
            changed = 0
            skipped = 0
            errors = 0
            details: list[dict[str, Any]] = []
            price_by_sku: dict[str, int] = {}

            started_at = datetime.utcnow().isoformat(timespec='seconds')
            self._last_status[store_id] = {'running': True, 'started_at': started_at, 'processed_now': 0, 'total': total, 'percent': 0, 'reason': reason, 'changed': 0, 'skipped': 0, 'errors': 0}

            async def guarded(pid: int):
                async with semaphore:
                    if store_id in self._stop_flags:
                        return {'product_id': pid, 'sku': '', 'name': '', 'old_price': 0, 'new_price': 0, 'delta': 0, 'changed': False, 'reason': 'Остановлено пользователем', 'status': 'stopped'}
                    return await self._process_one(pid, bool(update_local_prices))

            tasks = [asyncio.create_task(guarded(pid)) for pid in product_ids]
            for task in asyncio.as_completed(tasks):
                item = await task
                processed += 1
                details.append(item)
                sku = str(item.get('sku') or '')
                if sku:
                    price_by_sku[sku] = int(item.get('new_price') or item.get('old_price') or 0)
                if item.get('status') == 'error':
                    errors += 1
                elif item.get('changed'):
                    changed += 1
                else:
                    skipped += 1
                self._last_status[store_id] = {
                    'running': True,
                    'started_at': started_at,
                    'processed_now': processed,
                    'total': total,
                    'percent': round(processed / max(1, total) * 100, 1),
                    'reason': reason,
                    'last_sku': sku,
                    'changed': changed,
                    'skipped': skipped,
                    'errors': errors,
                    'concurrency': concurrency,
                }
                if store_id in self._stop_flags:
                    break

            products = db.query(Product).filter(Product.id.in_(product_ids)).order_by(Product.id.asc()).all()
            record = xml_feed_service.save_feed(
                store=store,
                products=products,
                price_by_sku=price_by_sku,
                warehouse_id=warehouse_id,
                processed=processed,
                changed=changed,
                skipped=skipped,
                limit_count=limit_count,
                q_filter=q_filter or reason,
                details=details,
            )
            db.add(Alert(title='XML автопилот обновил прайс', body=f'{store.name}: товаров {processed}, новых цен {changed}, без изменений {skipped}, ошибок {errors}. Файл {record.get("filename")}.', type=AlertType.SYSTEM))
            db.commit()
            result = {'store_id': store_id, 'ok': True, 'feed_id': record.get('feed_id'), 'filename': record.get('filename'), 'processed': processed, 'changed': changed, 'skipped': skipped, 'errors': errors, 'updated_at': record.get('updated_at'), 'reason': reason}
            self._last_status[store_id] = {**result, 'running': False, 'percent': 100, 'concurrency': concurrency}
            return result

    def last_status(self, store_id: int | None) -> dict[str, Any] | None:
        if not store_id:
            return None
        return self._last_status.get(int(store_id))


autopilot_service = AutoPilotService()
