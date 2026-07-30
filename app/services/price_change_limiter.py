from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.task_log import TaskLog, TaskStatus


class PriceChangeLimiter:
    """Persistent per-store budget for price changes included in XML versions.

    The budget is calculated from successfully saved XML versions in PostgreSQL,
    so restarts and redeploys do not reset the 30-minute window.
    """

    def enabled(self) -> bool:
        return bool(getattr(settings, 'KASPI_PRICE_CHANGE_LIMIT_ENABLED', True))

    def window_minutes(self) -> int:
        return max(1, int(getattr(settings, 'KASPI_PRICE_CHANGE_WINDOW_MINUTES', 30) or 30))

    def hard_limit(self) -> int:
        return max(1, int(getattr(settings, 'KASPI_PRICE_CHANGE_LIMIT_PER_WINDOW', 250) or 250))

    def safety_reserve(self) -> int:
        reserve = max(0, int(getattr(settings, 'KASPI_PRICE_CHANGE_SAFETY_RESERVE', 10) or 0))
        return min(reserve, max(0, self.hard_limit() - 1))

    def effective_limit(self) -> int:
        return max(1, self.hard_limit() - self.safety_reserve())

    def usage(self, db: Session, store_id: int | None) -> dict[str, Any]:
        now = datetime.utcnow()
        window = self.window_minutes()
        hard_limit = self.hard_limit()
        effective_limit = self.effective_limit()
        if not self.enabled() or not store_id:
            return {
                'enabled': False,
                'window_minutes': window,
                'hard_limit': hard_limit,
                'safety_reserve': self.safety_reserve(),
                'effective_limit': effective_limit,
                'used': 0,
                'remaining': effective_limit,
                'reset_at': None,
            }

        cutoff = now - timedelta(minutes=window)
        logs = (
            db.query(TaskLog)
            .filter(
                TaskLog.task_name == 'xml_feed_version',
                TaskLog.status == TaskStatus.SUCCESS,
                TaskLog.created_at >= cutoff,
            )
            .order_by(TaskLog.created_at.asc())
            .all()
        )

        used = 0
        first_relevant_at: datetime | None = None
        for log in logs:
            try:
                payload = json.loads(log.payload_json or '{}')
            except Exception:
                continue
            if int(payload.get('store_id') or 0) != int(store_id):
                continue
            changed = max(0, int(payload.get('changed') or 0))
            if changed <= 0:
                continue
            used += changed
            if first_relevant_at is None:
                first_relevant_at = log.created_at

        remaining = max(0, effective_limit - used)
        reset_at = (first_relevant_at + timedelta(minutes=window)).isoformat(timespec='seconds') if first_relevant_at else None
        return {
            'enabled': True,
            'window_minutes': window,
            'hard_limit': hard_limit,
            'safety_reserve': self.safety_reserve(),
            'effective_limit': effective_limit,
            'used': used,
            'remaining': remaining,
            'reset_at': reset_at,
        }


price_change_limiter = PriceChangeLimiter()
