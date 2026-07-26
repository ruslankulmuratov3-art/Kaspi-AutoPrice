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
from app.services.xml_feed_service import xml_feed_service, XmlFeedError

logger = get_logger(__name__)


class AutoPilotService:
    """Автоматически пересобирает XML-прайс.

    Важная логика:
    - один раз импортируем ACTIVE.xlsx в базу;
    - дальше XML строится из товаров в базе, полный Excel каждый раз не нужен;
    - Kaspi забирает публичную ссылку /kaspi-feed/{store_id}.xml;
    - если XML устарел, сервис пересобирает его перед отдачей;
    - дополнительно есть фоновый цикл, пока Render-сервис бодрствует.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._started = False
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_status: dict[int, dict[str, Any]] = {}

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
        age_seconds = (datetime.now() - updated_at).total_seconds()
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
            db.commit()
            return results
        finally:
            db.close()

    async def rebuild_store_if_stale(self, db: Session, store_id: int, reason: str = 'pull_if_stale') -> dict[str, Any] | None:
        if not self.enabled():
            return None
        if not bool(getattr(settings, 'KASPI_XML_REBUILD_ON_PULL', True)):
            return None
        if not self.is_feed_stale(store_id):
            return None
        return await self.rebuild_store_feed(db, store_id, reason=reason)

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
            store = db.query(Store).filter(Store.id == store_id).first()
            if not store:
                raise XmlFeedError('Магазин не найден')

            products_query = db.query(Product).filter(
                Product.store_id == store_id,
                Product.auto_pricing_enabled == True,
                Product.status == ProductStatus.ACTIVE,
                Product.min_price > 0,
                Product.max_price > 0,
            )
            if q_filter.strip():
                # DB LIKE оставляем как быстрый фильтр; для каталога на 1-5 тыс товаров этого достаточно.
                like = f'%{q_filter.strip()}%'
                products_query = products_query.filter((Product.name.ilike(like)) | (Product.kaspi_sku.ilike(like)) | (Product.url.ilike(like)))
            max_products = int(getattr(settings, 'KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN', 5000) or 5000)
            if limit_count and int(limit_count) > 0:
                max_products = min(max_products, int(limit_count))
            products = products_query.order_by(Product.id.asc()).limit(max_products).all()
            if not products:
                raise XmlFeedError('Нет готовых товаров для XML. Сначала импортируй ACTIVE.xlsx и примени лимиты.')

            warehouse_id = (warehouse_id or '').strip() or str(getattr(settings, 'KASPI_AUTOPILOT_WAREHOUSE_ID', '') or 'PP1')
            if update_local_prices is None:
                update_local_prices = bool(getattr(settings, 'KASPI_AUTOPILOT_UPDATE_LOCAL_PRICE', True))

            price_by_sku: dict[str, int] = {}
            details: list[dict[str, Any]] = []
            changed = 0
            skipped = 0
            errors = 0

            self._last_status[store_id] = {
                'running': True,
                'started_at': datetime.now().isoformat(timespec='seconds'),
                'processed_now': 0,
                'total': len(products),
                'percent': 0,
                'reason': reason,
            }

            for index, product in enumerate(products, start=1):
                old_price = int(round(float(product.current_price or 0)))
                new_price = old_price
                decision_reason = 'Без изменения'
                status = 'same'
                try:
                    decision = await pricing_engine.preview_product(db, product)
                    decision_reason = str(decision.reason or '')
                    if decision.can_apply:
                        new_price = int(round(float(decision.suggested_price)))
                        status = 'changed' if new_price != old_price else 'same'
                    else:
                        status = 'skipped'
                except Exception as exc:
                    errors += 1
                    decision_reason = f'Ошибка расчёта: {exc}'[:300]
                    status = 'error'

                sku = str(product.kaspi_sku or '').strip()
                if sku:
                    price_by_sku[sku] = max(0, int(new_price))

                if new_price != old_price:
                    changed += 1
                    if update_local_prices:
                        product.current_price = new_price
                        db.add(product)
                        db.add(PriceHistory(
                            product_id=product.id,
                            old_price=old_price,
                            new_price=new_price,
                            reason=f'XML автопилот: {decision_reason}',
                            source='xml_autopilot_prepared',
                        ))
                else:
                    skipped += 1

                details.append({
                    'product_id': product.id,
                    'sku': sku,
                    'name': str(product.name or sku),
                    'old_price': old_price,
                    'new_price': new_price,
                    'delta': new_price - old_price,
                    'changed': new_price != old_price,
                    'reason': decision_reason,
                    'status': status,
                    'url': product.url or '',
                })

                self._last_status[store_id] = {
                    'running': True,
                    'started_at': self._last_status[store_id].get('started_at'),
                    'processed_now': index,
                    'total': len(products),
                    'percent': round(index / max(1, len(products)) * 100, 1),
                    'reason': reason,
                    'last_sku': sku,
                    'changed': changed,
                    'skipped': skipped,
                    'errors': errors,
                }

                delay = float(getattr(settings, 'KASPI_AUTOPILOT_DELAY_SECONDS', 0) or 0)
                if delay > 0:
                    await asyncio.sleep(min(delay, 10))

            record = xml_feed_service.save_feed(
                store=store,
                products=products,
                price_by_sku=price_by_sku,
                warehouse_id=warehouse_id,
                processed=len(products),
                changed=changed,
                skipped=skipped,
                limit_count=limit_count,
                q_filter=q_filter or reason,
                details=details,
            )
            db.add(Alert(
                title='XML автопилот обновил прайс',
                body=f'{store.name}: товаров {len(products)}, новых цен {changed}, без изменений {skipped}, ошибок {errors}. Файл {record.get("filename")}.',
                type=AlertType.SYSTEM,
            ))
            db.commit()

            result = {
                'store_id': store_id,
                'ok': True,
                'feed_id': record.get('feed_id'),
                'filename': record.get('filename'),
                'processed': len(products),
                'changed': changed,
                'skipped': skipped,
                'errors': errors,
                'updated_at': record.get('updated_at'),
                'reason': reason,
            }
            self._last_status[store_id] = {**result, 'running': False, 'percent': 100}
            return result

    def last_status(self, store_id: int | None) -> dict[str, Any] | None:
        if not store_id:
            return None
        return self._last_status.get(int(store_id))


autopilot_service = AutoPilotService()
