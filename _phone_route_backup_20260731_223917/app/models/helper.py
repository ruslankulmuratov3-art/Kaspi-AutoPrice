from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class HelperSession(Base, TimestampMixin):
    """Short-lived consent session used by a browser/companion helper.

    Only a SHA-256 token hash is stored. The URL token grants access to competitor
    tasks for one store and cannot open the operator dashboard.
    """

    __tablename__ = 'helper_sessions'

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    created_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    status = Column(String(24), default='active', nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    consented = Column(Boolean, default=False, nullable=False)
    total_assigned = Column(Integer, default=0, nullable=False)
    total_completed = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, default='')
    ip_address = Column(String(120), default='')
    user_agent = Column(Text, default='')

    store = relationship('Store')
    created_by = relationship('User')
