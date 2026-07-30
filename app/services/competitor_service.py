from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.autopilot import CompetitorSnapshot, CompetitorSourceState
from app.models.competitor import CompetitorOffer
from app.models.product import Product
from app.services.kaspi_client import KaspiApiError, KaspiHttpError, KaspiOffer, kaspi_client

logger = get_logger(__name__)


@dataclass(slots=True)
class CompetitorResult:
    offers: list[KaspiOffer]
    source: str
    cache_state: str
    fetched_at: datetime | None
    error: str = ''
    http_status: int | None = None


class CompetitorUnavailable(RuntimeError):
    def __init__(self, message: str, *, cooldown_until: datetime | None = None, http_status: int | None = None):
        super().__init__(message)
        self.cooldown_until = cooldown_until
        self.http_status = http_status


class CompetitorService:
    SOURCE_KEY = 'kaspi_public_offers'

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(max(1, min(settings.KASPI_AUTOPILOT_CONCURRENCY, 3)))
        self._product_locks: dict[str, asyncio.Lock] = {}
        self._rate_lock = asyncio.Lock()
        self._request_times: list[float] = []

    def _lock(self, product_key: str) -> asyncio.Lock:
        return self._product_locks.setdefault(product_key, asyncio.Lock())

    def _state(self, db: Session) -> CompetitorSourceState:
        row = db.query(CompetitorSourceState).filter(CompetitorSourceState.source_key == self.SOURCE_KEY).first()
        if not row:
            row = CompetitorSourceState(source_key=self.SOURCE_KEY, state='closed')
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def state_info(self, db: Session) -> dict[str, Any]:
        row = self._state(db)
        now = datetime.utcnow()
        cooldown = row.cooldown_until if row.cooldown_until and row.cooldown_until > now else None
        return {
            'state': 'open' if cooldown else row.state,
            'failure_count': row.failure_count,
            'cooldown_until': cooldown.isoformat(timespec='seconds') if cooldown else None,
            'last_http_status': row.last_http_status,
            'last_error': row.last_error,
            'last_success_at': row.last_success_at.isoformat(timespec='seconds') if row.last_success_at else None,
        }

    def is_open(self, db: Session) -> tuple[bool, datetime | None]:
        row = self._state(db)
        if row.cooldown_until and row.cooldown_until > datetime.utcnow():
            return True, row.cooldown_until
        if row.state == 'open':
            row.state = 'half_open'
            row.cooldown_until = None
            db.add(row)
            db.commit()
        return False, None

    def _snapshot(self, db: Session, product: Product) -> CompetitorSnapshot | None:
        return db.query(CompetitorSnapshot).filter(CompetitorSnapshot.product_id == product.id).first()

    def _offers_from_snapshot(self, snapshot: CompetitorSnapshot) -> list[KaspiOffer]:
        try:
            rows = json.loads(snapshot.offers_json or '[]')
        except Exception:
            rows = []
        return [KaspiOffer(str(x.get('seller_name') or ''), str(x.get('seller_id') or ''), float(x.get('price') or 0), int(x.get('delivery_days') or 0), int(x.get('position') or 0)) for x in rows if float(x.get('price') or 0) > 0]

    def cached(self, db: Session, product: Product, *, allow_stale: bool = False) -> CompetitorResult | None:
        snapshot = self._snapshot(db, product)
        if not snapshot or not snapshot.fetched_at:
            return None
        now = datetime.utcnow()
        fresh_until = snapshot.expires_at or (snapshot.fetched_at + timedelta(minutes=settings.KASPI_COMPETITOR_CACHE_MINUTES))
        stale_until = snapshot.fetched_at + timedelta(minutes=settings.KASPI_COMPETITOR_STALE_CACHE_MINUTES)
        if now <= fresh_until:
            state = 'fresh'
        elif allow_stale and now <= stale_until:
            state = 'stale'
        else:
            return None
        offers = self._offers_from_snapshot(snapshot)
        if not offers:
            return None
        return CompetitorResult(offers=offers, source=snapshot.source, cache_state=state, fetched_at=snapshot.fetched_at, error=snapshot.last_error or '', http_status=snapshot.http_status)

    async def _rate_limit(self) -> None:
        import time
        rpm = max(1, int(settings.KASPI_PUBLIC_OFFERS_REQUESTS_PER_MINUTE or 1))
        async with self._rate_lock:
            now = time.monotonic()
            self._request_times = [x for x in self._request_times if now - x < 60]
            if len(self._request_times) >= rpm:
                wait_for = max(0.1, 60 - (now - self._request_times[0]))
                await asyncio.sleep(wait_for)
                now = time.monotonic()
                self._request_times = [x for x in self._request_times if now - x < 60]
            self._request_times.append(time.monotonic())

    def _open_circuit(self, db: Session, exc: Exception) -> datetime:
        row = self._state(db)
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_failure_at = datetime.utcnow()
        status = getattr(exc, 'status_code', None)
        row.last_http_status = status
        row.last_error = str(exc)[:500]
        threshold = max(1, int(settings.KASPI_PUBLIC_OFFERS_FAILURE_THRESHOLD or 2))
        retry_after = getattr(exc, 'retry_after', None)
        base_minutes = max(1, int(settings.KASPI_PUBLIC_OFFERS_COOLDOWN_MINUTES or 60))
        max_minutes = max(base_minutes, int(settings.KASPI_PUBLIC_OFFERS_MAX_COOLDOWN_MINUTES or 720))
        multiplier = 2 ** max(0, row.failure_count - threshold)
        minutes = min(max_minutes, max(base_minutes, int((retry_after or 0) / 60) + 1) * multiplier)
        until = datetime.utcnow() + timedelta(minutes=minutes)
        if row.failure_count >= threshold or status in (403, 405, 429):
            row.state = 'open'
            row.cooldown_until = until
        db.add(row)
        db.commit()
        return until

    def _mark_success(self, db: Session) -> None:
        row = self._state(db)
        row.state = 'closed'
        row.failure_count = 0
        row.cooldown_until = None
        row.last_error = ''
        row.last_http_status = 200
        row.last_success_at = datetime.utcnow()
        db.add(row)
        db.commit()

    def _save_snapshot(self, db: Session, product: Product, offers: list[KaspiOffer]) -> CompetitorSnapshot:
        snapshot = self._snapshot(db, product) or CompetitorSnapshot(store_id=product.store_id, product_id=product.id)
        now = datetime.utcnow()
        snapshot.public_product_id = kaspi_client.extract_public_product_id(product)
        snapshot.source = self.SOURCE_KEY
        snapshot.status = 'ok'
        snapshot.minimum_price = min(o.price for o in offers)
        snapshot.offers_json = json.dumps([{'seller_name': o.seller_name, 'seller_id': o.seller_id, 'price': o.price, 'delivery_days': o.delivery_days, 'position': o.position} for o in offers], ensure_ascii=False)
        snapshot.fetched_at = now
        snapshot.expires_at = now + timedelta(minutes=max(1, settings.KASPI_COMPETITOR_CACHE_MINUTES))
        snapshot.http_status = 200
        snapshot.last_error = ''
        db.add(snapshot)
        db.query(CompetitorOffer).filter(CompetitorOffer.product_id == product.id).delete(synchronize_session=False)
        for offer in offers:
            db.add(CompetitorOffer(
                product_id=product.id,
                seller_name=offer.seller_name,
                seller_id=offer.seller_id,
                price=offer.price,
                delivery_days=offer.delivery_days,
                position=offer.position,
            ))
        product.last_competitor_checked_at = now
        product.last_competitor_price = snapshot.minimum_price
        product.last_autopilot_error = ''
        db.add(product)
        db.commit()
        return snapshot

    async def get(self, db: Session, product: Product, *, force: bool = False) -> CompetitorResult:
        if not force:
            cached = self.cached(db, product)
            if cached:
                return cached
        is_open, until = self.is_open(db)
        if is_open:
            stale = self.cached(db, product, allow_stale=bool(settings.KASPI_USE_STALE_COMPETITOR_CACHE_ON_ERROR))
            if stale:
                stale.error = f'Источник на паузе до {until.isoformat(timespec="minutes") if until else "позже"}'
                return stale
            raise CompetitorUnavailable('Конкуренты временно недоступны. Цена оставлена без изменений.', cooldown_until=until)

        key = str(product.product_id or product.kaspi_sku or product.id)
        async with self._lock(key):
            if not force:
                cached = self.cached(db, product)
                if cached:
                    return cached
            async with self._semaphore:
                await self._rate_limit()
                try:
                    offers = await kaspi_client.get_product_offers(product, product.store)
                    self._save_snapshot(db, product, offers)
                    self._mark_success(db)
                    return CompetitorResult(offers=offers, source=self.SOURCE_KEY, cache_state='live', fetched_at=datetime.utcnow())
                except (KaspiHttpError, KaspiApiError) as exc:
                    until = self._open_circuit(db, exc)
                    snapshot = self._snapshot(db, product)
                    if snapshot:
                        snapshot.status = 'error'
                        snapshot.http_status = getattr(exc, 'status_code', None)
                        snapshot.last_error = str(exc)[:500]
                        db.add(snapshot)
                    product.last_autopilot_error = str(exc)[:500]
                    db.add(product)
                    db.commit()
                    stale = self.cached(db, product, allow_stale=bool(settings.KASPI_USE_STALE_COMPETITOR_CACHE_ON_ERROR))
                    if stale:
                        stale.error = str(exc)
                        return stale
                    raise CompetitorUnavailable('Конкуренты временно недоступны. Цена оставлена без изменений.', cooldown_until=until, http_status=getattr(exc, 'status_code', None)) from exc


competitor_service = CompetitorService()
