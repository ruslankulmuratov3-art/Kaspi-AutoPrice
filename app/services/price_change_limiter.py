from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.price_change import PendingPriceChange, PriceChangeEvent
from app.models.store import Store


class PriceChangeLimiter:
    """Persistent per-store price-change budget.

    The 250/30 rule is configurable because public Kaspi documentation does not expose a
    stable machine-readable limit. PostgreSQL row locking prevents two workers from spending
    the same budget simultaneously.
    """

    def enabled(self) -> bool:
        return bool(settings.KASPI_PRICE_CHANGE_LIMIT_ENABLED)

    def window_minutes(self) -> int:
        return max(1, int(settings.KASPI_PRICE_CHANGE_WINDOW_MINUTES or 30))

    def hard_limit(self) -> int:
        return max(1, int(settings.KASPI_PRICE_CHANGE_LIMIT_PER_WINDOW or 250))

    def safety_reserve(self) -> int:
        return min(max(0, int(settings.KASPI_PRICE_CHANGE_SAFETY_RESERVE or 0)), self.hard_limit() - 1)

    def effective_limit(self) -> int:
        return max(1, self.hard_limit() - self.safety_reserve())

    def _lock_store(self, db: Session, store_id: int) -> None:
        query = db.query(Store).filter(Store.id == int(store_id))
        try:
            query = query.with_for_update()
        except Exception:
            pass
        query.first()

    def usage(self, db: Session, store_id: int | None) -> dict[str, Any]:
        window = self.window_minutes()
        effective = self.effective_limit()
        if not self.enabled() or not store_id:
            return {'enabled': False, 'window_minutes': window, 'hard_limit': self.hard_limit(), 'safety_reserve': self.safety_reserve(), 'effective_limit': effective, 'used': 0, 'remaining': effective, 'reset_at': None, 'queued': 0}
        cutoff = datetime.utcnow() - timedelta(minutes=window)
        events = db.query(PriceChangeEvent).filter(PriceChangeEvent.store_id == int(store_id), PriceChangeEvent.created_at >= cutoff, PriceChangeEvent.status.in_(['prepared', 'active', 'requested'])).order_by(PriceChangeEvent.created_at.asc()).all()
        used = len(events)
        first = events[0].created_at if events else None
        queued = db.query(PendingPriceChange).filter(PendingPriceChange.store_id == int(store_id), PendingPriceChange.is_active == True, PendingPriceChange.status == 'queued').count()
        return {
            'enabled': True,
            'window_minutes': window,
            'hard_limit': self.hard_limit(),
            'safety_reserve': self.safety_reserve(),
            'effective_limit': effective,
            'used': used,
            'remaining': max(0, effective - used),
            'reset_at': (first + timedelta(minutes=window)).isoformat(timespec='seconds') if first else None,
            'queued': queued,
        }

    def allocate(self, db: Session, store_id: int, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        self._lock_store(db, store_id)
        budget = self.usage(db, store_id)
        remaining = int(budget['remaining']) if budget['enabled'] else len(candidates)
        allowed = candidates[:remaining]
        queued = candidates[remaining:]
        reset_at = None
        if budget.get('reset_at'):
            try:
                reset_at = datetime.fromisoformat(str(budget['reset_at']))
            except Exception:
                reset_at = None
        for item in queued:
            product_id = int(item['product_id'])
            row = db.query(PendingPriceChange).filter(PendingPriceChange.product_id == product_id).first()
            if not row:
                row = PendingPriceChange(store_id=int(store_id), product_id=product_id, requested_price=float(item['new_price']), old_price=float(item['old_price']))
            row.requested_price = float(item['new_price'])
            row.old_price = float(item['old_price'])
            row.reason = str(item.get('reason') or '')[:2000]
            row.status = 'queued'
            row.available_after = reset_at
            row.is_active = True
            db.add(row)
        db.flush()
        return allowed, queued, budget

    def record_applied(self, db: Session, store_id: int, feed_id: str, items: list[dict[str, Any]]) -> None:
        for item in items:
            product_id = int(item['product_id'])
            db.add(PriceChangeEvent(store_id=int(store_id), product_id=product_id, xml_feed_id=str(feed_id), old_price=float(item['old_price']), new_price=float(item['new_price']), source='xml', status='active', reason=str(item.get('reason') or '')[:2000], window_started_at=datetime.utcnow()))
            pending = db.query(PendingPriceChange).filter(PendingPriceChange.product_id == product_id).first()
            if pending:
                pending.status = 'applied'
                pending.is_active = False
                db.add(pending)


price_change_limiter = PriceChangeLimiter()
