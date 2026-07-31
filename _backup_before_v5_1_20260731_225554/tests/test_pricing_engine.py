from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.store import Store
from app.services.kaspi_client import KaspiOffer
from app.services.pricing_engine import PricingEngine


def make_product(price=10000, minimum=8000, maximum=14000, cost=7000):
    store = Store(name='OWN', merchant_id='301')
    product = Product(id=1, store_id=1, kaspi_sku='123_456', product_id='123', name='Товар', current_price=price, min_price=minimum, max_price=maximum, cost_price=cost, stock=5, status=ProductStatus.ACTIVE, auto_pricing_enabled=True)
    product.store = store
    return product


def test_price_below_competitor_respects_margin_floor():
    product = make_product()
    rule = PricingRule(strategy=PricingStrategy.BEAT_BY_STEP, beat_step=1, min_margin_percent=20, max_change_percent_per_run=50, is_enabled=True)
    decision = PricingEngine().decide(product, [KaspiOffer('OTHER', '2', 8200, 0, 1)], rule, source='test', cache_state='live')
    assert decision.can_apply
    assert decision.suggested_price == 8400  # 7000 + 20%
    assert 'марж' in decision.reason


def test_price_change_guard_blocks_large_jump():
    product = make_product(price=10000, minimum=1000, maximum=30000, cost=0)
    rule = PricingRule(strategy=PricingStrategy.BEAT_BY_STEP, beat_step=1, min_margin_percent=0, max_change_percent_per_run=5, is_enabled=True)
    decision = PricingEngine().decide(product, [KaspiOffer('OTHER', '2', 5000, 0, 1)], rule)
    assert not decision.can_apply
    assert decision.status == 'safe_skipped'


def test_own_store_is_excluded():
    product = make_product()
    rule = PricingRule(strategy=PricingStrategy.BEAT_BY_STEP, beat_step=1, min_margin_percent=0, max_change_percent_per_run=50, is_enabled=True)
    decision = PricingEngine().decide(product, [KaspiOffer('OWN', '301', 9000, 0, 1)], rule)
    assert not decision.can_apply
    assert decision.status == 'safe_skipped'
