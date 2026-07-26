from app.services.pricing_engine import pricing_engine
from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.services.kaspi_client import KaspiOffer


def test_beat_by_step_respects_min_price():
    product = Product(id=1, current_price=1000, min_price=950, max_price=2000, auto_pricing_enabled=True, status=ProductStatus.ACTIVE)
    rule = PricingRule(strategy=PricingStrategy.BEAT_BY_STEP, beat_step=100, is_enabled=True)
    offers = [KaspiOffer('A', 'a', 980, 1, 1)]
    decision = pricing_engine.decide(product, offers, rule)
    assert decision.suggested_price == 950
