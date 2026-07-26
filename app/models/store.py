from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class Store(Base, TimestampMixin):
    __tablename__ = 'stores'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(180), nullable=False)
    merchant_id = Column(String(80), nullable=False, index=True)
    city = Column(String(80), default='Алматы')
    api_token = Column(Text, default='')
    is_active = Column(Boolean, default=True, nullable=False)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    owner = relationship('User', back_populates='stores')
    products = relationship('Product', back_populates='store', cascade='all, delete-orphan')
