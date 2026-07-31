import enum
from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.mixins import TimestampMixin


class UserRole(str, enum.Enum):
    OWNER = 'owner'
    ADMIN = 'admin'
    MANAGER = 'manager'
    VIEWER = 'viewer'


class User(Base, TimestampMixin):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.ADMIN, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    full_name = Column(String(160), default='')
    avatar_url = Column(String(500), default='')
    auth_provider = Column(String(40), default='password', nullable=False)
    google_sub = Column(String(255), unique=True, nullable=True, index=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    stores = relationship('Store', back_populates='owner')
    audit_logs = relationship('AuditLog', back_populates='user')

    @property
    def display_name(self) -> str:
        return self.username or self.email
