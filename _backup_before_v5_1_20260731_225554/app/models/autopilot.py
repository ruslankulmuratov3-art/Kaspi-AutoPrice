from __future__ import annotations

import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class AutopilotJobStatus(str, enum.Enum):
    QUEUED = 'queued'
    RUNNING = 'running'
    PAUSED = 'paused'
    DONE = 'done'
    ERROR = 'error'
    CANCELLED = 'cancelled'


class AutopilotJob(Base, TimestampMixin):
    __tablename__ = 'autopilot_jobs'

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    status = Column(Enum(AutopilotJobStatus), default=AutopilotJobStatus.QUEUED, nullable=False, index=True)
    mode = Column(String(40), default='all', nullable=False)
    query_filter = Column(String(255), default='')
    requested_limit = Column(Integer, default=0)
    warehouse_id = Column(String(80), default='PP1')

    total = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    changed = Column(Integer, default=0)
    unchanged = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    queued_changes = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    cursor_product_id = Column(Integer, nullable=True)
    current_product_id = Column(Integer, nullable=True)

    stop_requested = Column(Boolean, default=False, nullable=False)
    heartbeat_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, default='')
    result_json = Column(Text, default='{}')
    worker_id = Column(String(120), default='')
    recovery_count = Column(Integer, default=0, nullable=False)
    recovery_notice_pending = Column(Boolean, default=False, nullable=False)

    store = relationship('Store')


class AutopilotJobItem(Base, TimestampMixin):
    """Persistent result for one product inside an autopilot job.

    These rows make pause/resume and process restarts safe: a job can continue from its
    cursor without losing decisions that were already calculated before the pause.
    """

    __tablename__ = 'autopilot_job_items'
    __table_args__ = (UniqueConstraint('job_id', 'product_id', name='uq_autopilot_job_product'),)

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey('autopilot_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    sku = Column(String(255), default='')
    product_name = Column(Text, default='')
    old_price = Column(Float, default=0.0)
    new_price = Column(Float, default=0.0)
    requested_price = Column(Float, nullable=True)
    competitor_price = Column(Float, nullable=True)
    changed = Column(Boolean, default=False, nullable=False)
    status = Column(String(40), default='safe_skipped', nullable=False, index=True)
    reason = Column(Text, default='')
    data_source = Column(String(80), default='')
    cache_state = Column(String(40), default='')
    error_message = Column(Text, default='')
    competitor_seller = Column(String(255), default='')
    competitor_seller_id = Column(String(255), default='')
    min_price = Column(Float, default=0.0)
    max_price = Column(Float, default=0.0)
    cost_price = Column(Float, default=0.0)
    margin_percent = Column(Float, default=0.0)
    step = Column(Float, default=0.0)
    http_status = Column(Integer, nullable=True)
    retry_at = Column(DateTime, nullable=True)
    source_device = Column(String(255), default='')
    xml_feed_id = Column(String(120), default='')

    job = relationship('AutopilotJob')
    store = relationship('Store')
    product = relationship('Product')


class CompetitorSnapshot(Base, TimestampMixin):
    __tablename__ = 'competitor_snapshots'
    __table_args__ = (UniqueConstraint('product_id', name='uq_competitor_snapshot_product'),)

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey('stores.id'), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    public_product_id = Column(String(120), default='', index=True)
    source = Column(String(80), default='public_offers')
    status = Column(String(40), default='ok', index=True)
    minimum_price = Column(Float, default=0.0)
    offers_json = Column(Text, default='[]')
    fetched_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    http_status = Column(Integer, nullable=True)
    last_error = Column(Text, default='')

    # Short-lived task lease used by multiple trusted local agents.
    lease_owner = Column(String(120), default='', index=True)
    lease_token = Column(String(80), default='', index=True)
    lease_started_at = Column(DateTime, nullable=True)
    lease_until = Column(DateTime, nullable=True, index=True)

    product = relationship('Product')
    store = relationship('Store')


class CompetitorSourceState(Base, TimestampMixin):
    __tablename__ = 'competitor_source_states'
    __table_args__ = (UniqueConstraint('source_key', name='uq_competitor_source_key'),)

    id = Column(Integer, primary_key=True, index=True)
    source_key = Column(String(160), nullable=False, index=True)
    state = Column(String(30), default='closed', nullable=False, index=True)
    failure_count = Column(Integer, default=0, nullable=False)
    cooldown_until = Column(DateTime, nullable=True)
    last_http_status = Column(Integer, nullable=True)
    last_error = Column(Text, default='')
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
