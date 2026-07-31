from __future__ import annotations

import enum

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class XmlFeedStatus(str, enum.Enum):
    CANDIDATE = 'candidate'
    ACTIVE = 'active'
    REJECTED = 'rejected'
    ARCHIVED = 'archived'


class XmlFeedVersion(Base, TimestampMixin):
    __tablename__ = 'xml_feed_versions'

    id = Column(Integer, primary_key=True, index=True)
    feed_id = Column(String(120), nullable=False, unique=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    status = Column(String(30), default=XmlFeedStatus.CANDIDATE.value, nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    merchant_id = Column(String(120), default='')
    warehouse_id = Column(String(80), default='PP1')
    xml_text = Column(Text, nullable=False)
    details_json = Column(Text, default='[]')
    product_count = Column(Integer, default=0)
    expected_count = Column(Integer, default=0)
    changed_count = Column(Integer, default=0)
    unchanged_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    queued_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    rejection_reason = Column(Text, default='')
    source = Column(String(80), default='manual')
    job_id = Column(Integer, nullable=True, index=True)
    is_active = Column(Boolean, default=False, nullable=False, index=True)

    store = relationship('Store')


class XmlFeedPull(Base, TimestampMixin):
    __tablename__ = 'xml_feed_pulls'

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    feed_id = Column(String(120), default='', index=True)
    path = Column(String(255), default='')
    ip_address = Column(String(120), default='')
    user_agent = Column(Text, default='')
    likely_kaspi = Column(Boolean, default=False, nullable=False)
    response_status = Column(Integer, default=200)

    store = relationship('Store')
