import enum
from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class ProductStatus(str, enum.Enum):
    ACTIVE = 'active'
    PAUSED = 'paused'
    OUT_OF_STOCK = 'out_of_stock'
    ARCHIVED = 'archived'


class Product(Base, TimestampMixin):
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    kaspi_sku = Column(String(120), nullable=False, index=True)
    product_id = Column(String(120), default='', index=True)  # public Kaspi product id, e.g. 108692468
    name = Column(String(255), nullable=False, index=True)
    model = Column(String(255), default='')
    category = Column(String(140), default='')
    brand = Column(String(120), default='')
    url = Column(Text, default='')
    current_price = Column(Float, default=0.0)
    min_price = Column(Float, default=0.0)
    max_price = Column(Float, default=0.0)
    cost_price = Column(Float, default=0.0)
    stock = Column(Integer, default=0)
    status = Column(Enum(ProductStatus), default=ProductStatus.ACTIVE, nullable=False)
    auto_pricing_enabled = Column(Boolean, default=True, nullable=False)

    # ACTIVE.xlsx sync + speed/cache metadata
    last_imported_at = Column(DateTime, nullable=True)
    last_seen_import_batch = Column(String(80), default='')
    missing_from_last_import = Column(Boolean, default=False, nullable=False)
    last_competitor_checked_at = Column(DateTime, nullable=True)
    last_competitor_price = Column(Float, default=0.0)
    last_autopilot_error = Column(Text, default='')

    store = relationship('Store', back_populates='products')
    pricing_rule = relationship('PricingRule', back_populates='product', uselist=False, cascade='all, delete-orphan')
    price_history = relationship('PriceHistory', back_populates='product', cascade='all, delete-orphan')
    competitor_offers = relationship('CompetitorOffer', back_populates='product', cascade='all, delete-orphan')
