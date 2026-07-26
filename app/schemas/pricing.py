from pydantic import BaseModel
from app.models.pricing_rule import PricingStrategy


class PricingRuleRead(BaseModel):
    id: int
    product_id: int
    strategy: PricingStrategy
    beat_step: float
    min_margin_percent: float
    max_change_percent_per_run: float
    ignore_sellers: str
    is_enabled: bool

    model_config = {'from_attributes': True}


class PricingPreview(BaseModel):
    product_id: int
    old_price: float
    suggested_price: float
    reason: str
    can_apply: bool


class BulkPriceRunResult(BaseModel):
    checked: int
    changed: int
    skipped: int
    errors: int
