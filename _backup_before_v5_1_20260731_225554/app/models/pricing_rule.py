import enum
from sqlalchemy import Column, Enum, Float, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class PricingStrategy(str, enum.Enum):
    MANUAL = 'manual'
    MATCH_MIN = 'match_min'
    BEAT_BY_STEP = 'beat_by_step'
    MARGIN_PROTECT = 'margin_protect'
    TOP_3_AVERAGE = 'top_3_average'


class PricingRule(Base, TimestampMixin):
    __tablename__ = 'pricing_rules'

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, unique=True)
    strategy = Column(Enum(PricingStrategy), default=PricingStrategy.BEAT_BY_STEP, nullable=False)
    beat_step = Column(Float, default=10.0)
    min_margin_percent = Column(Float, default=8.0)
    max_change_percent_per_run = Column(Float, default=10.0)
    ignore_sellers = Column(String(1000), default='')
    is_enabled = Column(Boolean, default=True, nullable=False)

    product = relationship('Product', back_populates='pricing_rule')
