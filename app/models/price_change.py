from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class PriceChangeEvent(Base, TimestampMixin):
    __tablename__ = 'price_change_events'

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    xml_feed_id = Column(String(120), default='', index=True)
    old_price = Column(Float, default=0.0)
    new_price = Column(Float, default=0.0)
    source = Column(String(80), default='xml')
    status = Column(String(40), default='prepared')
    reason = Column(Text, default='')
    window_started_at = Column(DateTime, nullable=True, index=True)

    store = relationship('Store')
    product = relationship('Product')


class PendingPriceChange(Base, TimestampMixin):
    __tablename__ = 'pending_price_changes'
    __table_args__ = (UniqueConstraint('product_id', name='uq_pending_price_product'),)

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    requested_price = Column(Float, nullable=False)
    old_price = Column(Float, nullable=False)
    reason = Column(Text, default='')
    status = Column(String(40), default='queued', index=True)
    available_after = Column(DateTime, nullable=True, index=True)
    attempts = Column(Integer, default=0)
    last_error = Column(Text, default='')
    is_active = Column(Boolean, default=True, nullable=False)

    store = relationship('Store')
    product = relationship('Product')
