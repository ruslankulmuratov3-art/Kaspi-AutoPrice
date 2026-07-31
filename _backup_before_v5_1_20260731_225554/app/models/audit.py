from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    action = Column(String(120), nullable=False)
    entity = Column(String(120), default='')
    entity_id = Column(String(80), default='')
    ip_address = Column(String(80), default='')
    meta_json = Column(Text, default='{}')

    user = relationship('User', back_populates='audit_logs')
