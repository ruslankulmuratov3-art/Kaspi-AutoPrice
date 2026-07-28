from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.alert import Alert, AlertType
from app.models.competitor import CompetitorOffer
from app.models.price_history import PriceHistory
from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.services.kaspi_client import KaspiApiError, KaspiOffer, kaspi_client

logger = get_logger(__name__)


@dataclass
class PricingDecision:
    product_id: int
    old_price: float
    suggested_price: float
    reason: str
    can_apply: bool


class PricingEngine:
    def clean_offers(self, product: Product, rule: PricingRule | None, offers: list[KaspiOffer]) -> list[KaspiOffer]:
        ignore = set()
        if rule and rule.ignore_sellers:
            ignore = {x.strip().lower() for x in rule.ignore_sellers.split(',') if x.strip()}
        if product.store:
            if product.store.name:
                ignore.add(product.store.name.strip().lower())
            if product.store.merchant_id:
                ignore.add(product.store.merchant_id.strip().lower())
        if settings.KASPI_COMPANY_NAME:
            ignore.add(settings.KASPI_COMPANY_NAME.strip().lower())
        if settings.KASPI_MERCHANT_ID:
            ignore.add(settings.KASPI_MERCHANT_ID.strip().lower())
        result: list[KaspiOffer] = []
        for offer in offers:
            seller_name = str(offer.seller_name or '').strip().lower()
            seller_id = str(offer.seller_id or '').strip().lower()
            if seller_name in ignore or seller_id in ignore:
                continue
            result.append(offer)
        return result

    def decide(self, product: Product, offers: list[KaspiOffer], rule: PricingRule | None) -> PricingDecision:
        old_price = float(product.current_price or 0)
        if product.status != ProductStatus.ACTIVE or not product.auto_pricing_enabled:
            return PricingDecision(product.id, old_price, old_price, 'Товар отключён от автоценообразования', False)
        if not rule or not rule.is_enabled or rule.strategy == PricingStrategy.MANUAL:
            return PricingDecision(product.id, old_price, old_price, 'Правило выключено или ручной режим', False)
        offers = self.clean_offers(product, rule, offers)
        if not offers:
            return PricingDecision(product.id, old_price, old_price, 'Нет предложений конкурентов', False)
        min_competitor = min(offer.price for offer in offers)
        candidate = old_price
        reason = 'Без изменения'
        if rule.strategy == PricingStrategy.MATCH_MIN:
            candidate = min_competitor
            reason = f'Сравняли с минимальной ценой конкурента {min_competitor:.0f}'
        elif rule.strategy == PricingStrategy.BEAT_BY_STEP:
            candidate = min_competitor - float(rule.beat_step or 0)
            reason = f'Сделали ниже минимального конкурента на {float(rule.beat_step or 0):.0f}'
        elif rule.strategy == PricingStrategy.TOP_3_AVERAGE:
            top = sorted(offer.price for offer in offers)[:3]
            candidate = sum(top) / len(top)
            reason = 'Взяли среднюю цену топ-3 конкурентов'
        elif rule.strategy == PricingStrategy.MARGIN_PROTECT:
            min_margin_price = float(product.cost_price or 0) * (1 + float(rule.min_margin_percent or 0) / 100)
            candidate = max(min_competitor - float(rule.beat_step or 0), min_margin_price)
            reason = f'Цена с защитой маржи {float(rule.min_margin_percent or 0):.1f}%'

        candidate = max(candidate, float(product.min_price or 0))
        if product.max_price and product.max_price > 0:
            candidate = min(candidate, float(product.max_price))
        candidate = round(candidate, 0)
        if old_price > 0:
            change_percent = abs(candidate - old_price) / old_price * 100
            max_change = min(float(rule.max_change_percent_per_run or 0), float(settings.MAX_PRICE_CHANGE_PERCENT or 30))
            if max_change > 0 and change_percent > max_change:
                return PricingDecision(product.id, old_price, old_price, f'Слишком большое изменение {change_percent:.1f}%, нужно ручное подтверждение', False)
        if candidate == old_price:
            return PricingDecision(product.id, old_price, old_price, 'Цена уже оптимальная', False)
        return PricingDecision(product.id, old_price, candidate, reason, True)

    def _cached_offers(self, db: Session, product: Product) -> list[KaspiOffer] | None:
        cache_minutes = int(getattr(settings, 'KASPI_COMPETITOR_CACHE_MINUTES', 15) or 0)
        if cache_minutes <= 0:
            return None
        checked_at = getattr(product, 'last_competitor_checked_at', None)
        if not checked_at:
            return None
        try:
            if datetime.utcnow() - checked_at > timedelta(minutes=cache_minutes):
                return None
        except Exception:
            return None
        rows = db.query(CompetitorOffer).filter(CompetitorOffer.product_id == product.id).order_by(CompetitorOffer.price.asc()).all()
        if not rows:
            return None
        return [KaspiOffer(r.seller_name, r.seller_id, float(r.price or 0), int(r.delivery_days or 0), int(r.position or 0)) for r in rows]

    async def refresh_competitors(self, db: Session, product: Product, *, force: bool = False) -> list[KaspiOffer]:
        if not force:
            cached = self._cached_offers(db, product)
            if cached is not None:
                return cached
        offers = await kaspi_client.get_product_offers(product, product.store)
        db.query(CompetitorOffer).filter(CompetitorOffer.product_id == product.id).delete()
        min_price = 0.0
        for offer in offers:
            min_price = min(min_price or offer.price, offer.price)
            db.add(CompetitorOffer(
                product_id=product.id,
                seller_name=offer.seller_name,
                seller_id=offer.seller_id,
                price=offer.price,
                delivery_days=offer.delivery_days,
                position=offer.position,
            ))
        product.last_competitor_checked_at = datetime.utcnow()
        product.last_competitor_price = float(min_price or 0)
        product.last_autopilot_error = ''
        db.add(product)
        db.commit()
        return offers

    async def preview_product(self, db: Session, product: Product, *, force_refresh: bool = False) -> PricingDecision:
        try:
            offers = await self.refresh_competitors(db, product, force=force_refresh)
        except KaspiApiError as exc:
            old_price = float(product.current_price or 0)
            product.last_autopilot_error = str(exc)[:1000]
            db.add(product)
            db.commit()
            return PricingDecision(product.id, old_price, old_price, f'Kaspi: {exc}', False)
        return self.decide(product, offers, product.pricing_rule)

    async def push_current_price(self, db: Session, product: Product) -> PricingDecision:
        old_price = float(product.current_price or 0)
        if old_price <= 0:
            return PricingDecision(product.id, old_price, old_price, 'Текущая цена должна быть больше 0', False)
        try:
            ok = await kaspi_client.update_price(product, product.store, old_price)
        except KaspiApiError as exc:
            return PricingDecision(product.id, old_price, old_price, f'Kaspi API: {exc}', False)
        if ok:
            db.add(PriceHistory(product_id=product.id, old_price=old_price, new_price=old_price, reason='Ручная отправка текущей цены в Kaspi API', source='manual'))
            db.add(Alert(title='Цена отправлена в Kaspi', body=f'{product.name}: {old_price:.0f} тг', type=AlertType.SYSTEM))
            db.commit()
        return PricingDecision(product.id, old_price, old_price, 'Текущая цена отправлена в реальный Kaspi API', True)

    async def apply_product(self, db: Session, product: Product) -> PricingDecision:
        decision = await self.preview_product(db, product)
        if not decision.can_apply:
            return decision
        ok = await kaspi_client.update_price(product, product.store, decision.suggested_price)
        if ok:
            old = product.current_price
            product.current_price = decision.suggested_price
            db.add(PriceHistory(product_id=product.id, old_price=old, new_price=decision.suggested_price, reason=decision.reason, source='auto'))
            db.add(Alert(title='Цена изменена', body=f'{product.name}: {old:.0f} → {decision.suggested_price:.0f} тг', type=AlertType.PRICE_CHANGED))
            db.add(product)
            db.commit()
            db.refresh(product)
        return decision


pricing_engine = PricingEngine()
