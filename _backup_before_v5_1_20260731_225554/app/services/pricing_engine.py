from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.services.competitor_service import CompetitorResult, CompetitorUnavailable, competitor_service
from app.services.kaspi_client import KaspiOffer


@dataclass(slots=True)
class PricingDecision:
    product_id: int
    old_price: float
    suggested_price: float
    reason: str
    can_apply: bool
    status: str = 'unchanged'
    competitor_price: float | None = None
    data_source: str = ''
    cache_state: str = ''
    details: dict[str, Any] | None = None


class PricingEngine:
    def clean_offers(self, product: Product, rule: PricingRule | None, offers: list[KaspiOffer]) -> list[KaspiOffer]:
        ignored = {x.strip().casefold() for x in str(rule.ignore_sellers or '').split(',') if x.strip()} if rule else set()
        if product.store:
            ignored.update({str(product.store.name or '').strip().casefold(), str(product.store.merchant_id or '').strip().casefold()})
        ignored.update({str(settings.KASPI_COMPANY_NAME or '').strip().casefold(), str(settings.KASPI_MERCHANT_ID or '').strip().casefold()})
        return [o for o in offers if str(o.seller_name or '').strip().casefold() not in ignored and str(o.seller_id or '').strip().casefold() not in ignored and float(o.price or 0) > 0]

    def decide(self, product: Product, offers: list[KaspiOffer], rule: PricingRule | None, *, source: str = '', cache_state: str = '') -> PricingDecision:
        old_price = float(product.current_price or 0)
        base = {'data_source': source, 'cache_state': cache_state}
        if product.status != ProductStatus.ACTIVE or not product.auto_pricing_enabled:
            return PricingDecision(product.id, old_price, old_price, 'Автопрайс выключен', False, 'safe_skipped', details={**base, 'reason_code': 'autopricing_disabled'})
        if not rule or not rule.is_enabled or rule.strategy == PricingStrategy.MANUAL:
            return PricingDecision(product.id, old_price, old_price, 'Ручной режим', False, 'safe_skipped', details={**base, 'reason_code': 'manual_mode'})
        clean = self.clean_offers(product, rule, offers)
        if not clean:
            return PricingDecision(product.id, old_price, old_price, 'Подходящие конкуренты не найдены', False, 'safe_skipped', details={**base, 'reason_code': 'no_competitors'})

        best_offer = min(clean, key=lambda o: float(o.price or 0))
        competitor_price = float(best_offer.price)
        base = {**base, 'competitor_seller': best_offer.seller_name, 'competitor_seller_id': best_offer.seller_id}
        candidate = old_price
        reason = 'Цена уже оптимальная'
        if rule.strategy == PricingStrategy.MATCH_MIN:
            candidate = competitor_price
            reason = f'Цена равна конкуренту {competitor_price:.0f} ₸'
        elif rule.strategy == PricingStrategy.BEAT_BY_STEP:
            step = max(0.0, float(rule.beat_step or 0))
            candidate = competitor_price - step
            reason = f'Ниже конкурента на {step:.0f} ₸'
        elif rule.strategy == PricingStrategy.TOP_3_AVERAGE:
            top = sorted(float(o.price) for o in clean)[:3]
            candidate = sum(top) / len(top)
            reason = 'Средняя цена топ-3 конкурентов'
        elif rule.strategy == PricingStrategy.MARGIN_PROTECT:
            step = max(0.0, float(rule.beat_step or 0))
            candidate = competitor_price - step
            reason = f'Ниже конкурента на {step:.0f} ₸ с защитой маржи'

        floors = [float(product.min_price or 0)]
        cost = float(product.cost_price or 0)
        margin = max(0.0, float(rule.min_margin_percent or 0))
        if cost > 0:
            floors.append(cost * (1 + margin / 100))
        floor = max(floors)
        if candidate < floor:
            candidate = floor
            reason = 'Достигнута минимальная безопасная цена или защита маржи'
            base = {**base, 'reason_code': 'minimum_or_margin'}

        ceiling = float(product.max_price or 0)
        if ceiling > 0 and candidate > ceiling:
            candidate = ceiling
            reason = 'Достигнута максимальная цена'
            base = {**base, 'reason_code': 'maximum'}

        candidate = float(round(candidate))
        if candidate <= 0:
            return PricingDecision(product.id, old_price, old_price, 'Некорректная рассчитанная цена', False, 'error', competitor_price, source, cache_state, base)

        if old_price > 0:
            change_percent = abs(candidate - old_price) / old_price * 100
            max_change = min(max(0.0, float(rule.max_change_percent_per_run or 0)), max(0.0, float(settings.MAX_PRICE_CHANGE_PERCENT or 30)))
            if max_change > 0 and change_percent > max_change:
                return PricingDecision(product.id, old_price, old_price, f'Изменение {change_percent:.1f}% выше лимита {max_change:.1f}%', False, 'safe_skipped', competitor_price, source, cache_state, {**base, 'change_percent': change_percent, 'reason_code': 'max_change_guard'})

        if candidate == old_price:
            return PricingDecision(product.id, old_price, old_price, 'Цена уже оптимальная', False, 'unchanged', competitor_price, source, cache_state, {**base, 'reason_code': 'already_optimal'})
        if cache_state in ('stale', 'fresh') and source:
            reason += ' · использован кэш'
        return PricingDecision(product.id, old_price, candidate, reason, True, 'changed', competitor_price, source, cache_state, {**base, 'reason_code': 'price_changed'})

    async def refresh_competitors(self, db: Session, product: Product, *, force: bool = False) -> list[KaspiOffer]:
        result = await competitor_service.get(db, product, force=force)
        return result.offers

    async def preview_product(self, db: Session, product: Product, *, force_refresh: bool = False) -> PricingDecision:
        old_price = float(product.current_price or 0)
        try:
            result: CompetitorResult = await competitor_service.get(db, product, force=force_refresh)
        except CompetitorUnavailable as exc:
            product.last_autopilot_error = str(exc)[:500]
            db.add(product)
            db.commit()
            return PricingDecision(product.id, old_price, old_price, str(exc), False, 'safe_skipped', data_source='unavailable', details={'cooldown_until': exc.cooldown_until.isoformat(timespec='seconds') if exc.cooldown_until else None, 'http_status': exc.http_status})
        decision = self.decide(product, result.offers, product.pricing_rule, source=result.source, cache_state=result.cache_state)
        if result.error:
            decision.details = {**(decision.details or {}), 'source_error': result.error}
        product.last_autopilot_error = '' if decision.status != 'error' else decision.reason
        db.add(product)
        db.commit()
        return decision

    async def push_current_price(self, db: Session, product: Product) -> PricingDecision:
        old_price = float(product.current_price or 0)
        return PricingDecision(product.id, old_price, old_price, 'Прямой API цены не подтверждён. Цена будет передана полным XML', False, 'safe_skipped')

    async def apply_product(self, db: Session, product: Product) -> PricingDecision:
        return await self.preview_product(db, product)


pricing_engine = PricingEngine()
