import enum
from sqlalchemy import Boolean, Column, Enum, Integer, String, Text
from app.core.database import Base
from app.models.mixins import TimestampMixin


class AlertType(str, enum.Enum):
    PRICE_CHANGED = 'price_changed'
    PRICE_LIMIT = 'price_limit'
    API_ERROR = 'api_error'
    SYSTEM = 'system'


class Alert(Base, TimestampMixin):
    __tablename__ = 'alerts'

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, default='')
    type = Column(Enum(AlertType), default=AlertType.SYSTEM, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
