from __future__ import annotations

import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class InviteKind(str, enum.Enum):
    ACCOUNT = 'account'
    DEVICE = 'device'


class InviteCode(Base, TimestampMixin):
    __tablename__ = 'invite_codes'

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(Enum(InviteKind), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False, unique=True, index=True)
    code_prefix = Column(String(24), nullable=False, index=True)
    created_by_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    assigned_user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    note = Column(String(255), default='')
    max_uses = Column(Integer, default=1, nullable=False)
    used_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_by = relationship('User', foreign_keys=[created_by_id])
    assigned_user = relationship('User', foreign_keys=[assigned_user_id])


class AgentDevice(Base, TimestampMixin):
    __tablename__ = 'agent_devices'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    device_key = Column(String(64), nullable=False, unique=True, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    token_prefix = Column(String(18), nullable=False, index=True)
    platform = Column(String(80), default='unknown')
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    last_seen_at = Column(DateTime, nullable=True, index=True)
    last_ip = Column(String(80), default='')
    last_user_agent = Column(Text, default='')
    tasks_requested = Column(Integer, default=0, nullable=False)
    results_ok = Column(Integer, default=0, nullable=False)
    results_error = Column(Integer, default=0, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship('User')
