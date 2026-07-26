from dataclasses import dataclass
from sqlalchemy.orm import Session
from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.price_history import PriceHistory
from app.models.competitor import CompetitorOffer
from app.models.alert import Alert, AlertType
from app.services.kaspi_client import KaspiOffer, kaspi_client, KaspiApiError
from app.core.config import settings
from app.core.logging import get_logger

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
            candidate = min_competitor - rule.beat_step
            reason = f'Сделали ниже минимального конкурента на {rule.beat_step:.0f}'
        elif rule.strategy == PricingStrategy.TOP_3_AVERAGE:
            top = sorted(offer.price for offer in offers)[:3]
            candidate = sum(top) / len(top)
            reason = 'Взяли среднюю цену топ-3 конкурентов'
        elif rule.strategy == PricingStrategy.MARGIN_PROTECT:
            min_margin_price = product.cost_price * (1 + rule.min_margin_percent / 100)
            candidate = max(min_competitor - rule.beat_step, min_margin_price)
            reason = f'Цена с защитой маржи {rule.min_margin_percent:.1f}%'

        candidate = max(candidate, product.min_price or 0)
        if product.max_price and product.max_price > 0:
            candidate = min(candidate, product.max_price)
        candidate = round(candidate, 0)

        if old_price > 0:
            change_percent = abs(candidate - old_price) / old_price * 100
            max_change = min(rule.max_change_percent_per_run, settings.MAX_PRICE_CHANGE_PERCENT)
            if change_percent > max_change:
                return PricingDecision(product.id, old_price, old_price, f'Слишком большое изменение {change_percent:.1f}%, нужно ручное подтверждение', False)
        if candidate == old_price:
            return PricingDecision(product.id, old_price, old_price, 'Цена уже оптимальная', False)
        return PricingDecision(product.id, old_price, candidate, reason, True)

    async def refresh_competitors(self, db: Session, product: Product) -> list[KaspiOffer]:
        offers = await kaspi_client.get_product_offers(product, product.store)
        db.query(CompetitorOffer).filter(CompetitorOffer.product_id == product.id).delete()
        for offer in offers:
            db.add(CompetitorOffer(
                product_id=product.id,
                seller_name=offer.seller_name,
                seller_id=offer.seller_id,
                price=offer.price,
                delivery_days=offer.delivery_days,
                position=offer.position,
            ))
        db.commit()
        return offers

    async def preview_product(self, db: Session, product: Product) -> PricingDecision:
        try:
            offers = await self.refresh_competitors(db, product)
        except KaspiApiError as exc:
            old_price = float(product.current_price or 0)
            return PricingDecision(product.id, old_price, old_price, f'Kaspi API: {exc}', False)
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
