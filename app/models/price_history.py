from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class PriceHistory(Base, TimestampMixin):
    __tablename__ = 'price_history'

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    old_price = Column(Float, default=0.0)
    new_price = Column(Float, default=0.0)
    reason = Column(Text, default='')
    source = Column(String(80), default='system')

    product = relationship('Product', back_populates='price_history')
