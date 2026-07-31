from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class CompetitorOffer(Base, TimestampMixin):
    __tablename__ = 'competitor_offers'

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    seller_name = Column(String(255), nullable=False)
    seller_id = Column(String(100), default='')
    price = Column(Float, nullable=False)
    delivery_days = Column(Integer, default=0)
    position = Column(Integer, default=0)

    product = relationship('Product', back_populates='competitor_offers')
