from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.autopilot import AutopilotJobItem, CompetitorSnapshot
from app.models.product import Product
from app.services.competitor_service import competitor_service
from app.services.pricing_engine import pricing_engine


SOURCE_LABELS = {
    '': 'Не указан',
    'unavailable': 'На момент расчёта данных не было',
    'kaspi_public_offers': 'Прямой запрос Kaspi',
    'public_offers': 'Прямой запрос Kaspi',
    'local_agent': 'Компьютер-помощник',
    'browser_helper': 'Телефон-помощник',
    'helper': 'Телефон-помощник',
    'manual': 'Ручной расчёт',
}

CACHE_LABELS = {
    '': '—',
    'live': 'получено сейчас',
    'fresh': 'свежие данные',
    'stale': 'сохранённый кэш',
    'missing': 'данных не было',
}


class ResultViewService:
    """Build human-friendly views without rewriting historical job results.

    A completed job is an audit record. If competitors were fetched later, the old
    row must stay unchanged, but the UI should clearly show that current data is now
    available and offer an explicit recalculation action.
    """

    @staticmethod
    def _source_label(value: str | None) -> str:
        key = str(value or '').strip()
        return SOURCE_LABELS.get(key, key.replace('_', ' ').strip().capitalize() or 'Не указан')

    @staticmethod
    def _cache_label(value: str | None) -> str:
        key = str(value or '').strip()
        return CACHE_LABELS.get(key, key.replace('_', ' ').strip().capitalize() or '—')

    @staticmethod
    def _historical_unavailable(row: AutopilotJobItem) -> bool:
        source = str(row.data_source or '').casefold()
        cache = str(row.cache_state or '').casefold()
        reason = str(row.reason or '').casefold()
        return bool(
            source == 'unavailable'
            or cache == 'missing'
            or (
                row.status == 'safe_skipped'
                and row.competitor_price is None
                and any(token in reason for token in ('недоступ', 'нет сохранённых данных', 'данные конкурентов пока не получены'))
            )
        )

    def product_snapshot_view(self, db: Session, product: Product) -> dict[str, Any]:
        snapshot = (
            db.query(CompetitorSnapshot)
            .filter(CompetitorSnapshot.product_id == int(product.id))
            .first()
        )
        if not snapshot or not snapshot.fetched_at:
            return {
                'available': False,
                'offer_count': 0,
                'minimum_price': None,
                'source': '',
                'source_label': 'Данных пока нет',
                'cache_state': 'missing',
                'cache_label': 'данных пока нет',
                'fetched_at': None,
                'decision': None,
            }

        offers = competitor_service._offers_from_snapshot(snapshot)
        clean_offers = pricing_engine.clean_offers(product, product.pricing_rule, offers)
        now = datetime.utcnow()
        cache_state = 'fresh' if not snapshot.expires_at or snapshot.expires_at >= now else 'stale'
        minimum_price = min((float(offer.price or 0) for offer in clean_offers), default=None)
        decision = None
        if clean_offers:
            decision = pricing_engine.decide(
                product,
                clean_offers,
                product.pricing_rule,
                source=str(snapshot.source or ''),
                cache_state=cache_state,
            )
        return {
            'available': bool(clean_offers),
            'offer_count': len(clean_offers),
            'minimum_price': minimum_price,
            'source': str(snapshot.source or ''),
            'source_label': self._source_label(snapshot.source),
            'cache_state': cache_state,
            'cache_label': self._cache_label(cache_state),
            'fetched_at': snapshot.fetched_at,
            'http_status': snapshot.http_status,
            'status': snapshot.status,
            'decision': decision,
        }

    def build(self, db: Session, rows: Iterable[AutopilotJobItem]) -> list[dict[str, Any]]:
        rows = list(rows)
        product_ids = sorted({int(row.product_id) for row in rows if row.product_id})
        products = {
            int(product.id): product
            for product in db.query(Product).filter(Product.id.in_(product_ids)).all()
        } if product_ids else {}

        result: list[dict[str, Any]] = []
        for row in rows:
            data = {column.name: getattr(row, column.name) for column in AutopilotJobItem.__table__.columns}
            product = products.get(int(row.product_id))
            current = self.product_snapshot_view(db, product) if product else {
                'available': False,
                'offer_count': 0,
                'minimum_price': None,
                'source_label': 'Товар удалён',
                'cache_label': '—',
                'fetched_at': None,
                'decision': None,
            }
            historical_unavailable = self._historical_unavailable(row)
            current_is_newer = bool(
                current.get('fetched_at')
                and row.updated_at
                and current['fetched_at'] > row.updated_at
            )

            display_reason = str(row.reason or 'Причина не записана')
            if historical_unavailable and current.get('available'):
                display_reason = (
                    'На момент этого запуска данных конкурентов ещё не было. '
                    'Сейчас данные уже получены — товар можно пересчитать отдельно.'
                )

            current_decision = current.get('decision')
            data.update({
                'product_url': f'/products/{row.product_id}' if product else '',
                'historical_unavailable': historical_unavailable,
                'display_reason': display_reason,
                'source_label': self._source_label(row.data_source),
                'cache_label': self._cache_label(row.cache_state),
                'current_available': bool(current.get('available')),
                'current_is_newer': current_is_newer,
                'current_offer_count': int(current.get('offer_count') or 0),
                'current_min_price': current.get('minimum_price'),
                'current_source_label': current.get('source_label') or '—',
                'current_cache_label': current.get('cache_label') or '—',
                'current_fetched_at': current.get('fetched_at'),
                'current_preview_status': getattr(current_decision, 'status', '') if current_decision else '',
                'current_preview_reason': getattr(current_decision, 'reason', '') if current_decision else '',
                'current_preview_price': getattr(current_decision, 'suggested_price', None) if current_decision else None,
                'can_recalculate': bool(product and current.get('available')),
            })
            result.append(data)
        return result


result_view_service = ResultViewService()
