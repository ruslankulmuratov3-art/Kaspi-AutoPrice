import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.autopilot import AutopilotJob, AutopilotJobStatus, CompetitorSnapshot
from app.models.helper import HelperSession
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.models.user import User, UserRole
from app.services.autopilot_service import AutoPilotService
from app.services.helper_session_service import helper_session_service
from app.services.incremental_pricing_service import IncrementalPricingService
import app.services.autopilot_service as autopilot_module
import app.services.incremental_pricing_service as incremental_module


def make_session(tmp_path, name='v5.db'):
    engine = create_engine(f'sqlite:///{tmp_path / name}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_helper_session_stores_only_hash_and_expires(tmp_path):
    Session = make_session(tmp_path)
    db = Session()
    user = User(email='owner@example.com', username='owner', password_hash='x', role=UserRole.OWNER)
    db.add(user)
    db.flush()
    store = Store(name='Shop', merchant_id='301', owner_id=user.id)
    db.add(store)
    db.commit()

    created = helper_session_service.create(db, store_id=store.id, user_id=user.id)
    assert created.token not in created.row.token_hash
    assert len(created.row.token_hash) == 64
    assert helper_session_service.get(db, created.token).id == created.row.id

    created.row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.add(created.row)
    db.commit()
    expired = helper_session_service.get(db, created.token, require_active=False)
    assert expired.status == 'expired'
    db.close()


def test_worker_crash_changes_job_to_error(tmp_path, monkeypatch):
    Session = make_session(tmp_path, 'worker.db')
    db = Session()
    store = Store(name='Shop', merchant_id='301')
    db.add(store)
    db.flush()
    job = AutopilotJob(store_id=store.id, status=AutopilotJobStatus.RUNNING)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    monkeypatch.setattr(autopilot_module, 'SessionLocal', Session)
    service = AutoPilotService()

    async def explode(_job_id):
        raise RuntimeError('worker exploded')

    monkeypatch.setattr(service, '_run_job', explode)
    asyncio.run(service._execute_job(job_id))

    db = Session()
    saved = db.query(AutopilotJob).filter_by(id=job_id).one()
    assert saved.status == AutopilotJobStatus.ERROR
    assert 'worker exploded' in saved.error_message
    db.close()


def test_incremental_cached_result_updates_one_product(tmp_path, monkeypatch):
    Session = make_session(tmp_path, 'incremental.db')
    db = Session()
    store = Store(name='OWN', merchant_id='301')
    db.add(store)
    db.flush()
    product = Product(
        store_id=store.id,
        kaspi_sku='110676037_1',
        product_id='110676037',
        name='Product',
        current_price=10000,
        min_price=8000,
        max_price=12000,
        cost_price=0,
        stock=3,
        status=ProductStatus.ACTIVE,
        auto_pricing_enabled=True,
    )
    db.add(product)
    db.flush()
    db.add(PricingRule(product_id=product.id, strategy=PricingStrategy.BEAT_BY_STEP, beat_step=1, min_margin_percent=0, max_change_percent_per_run=50, is_enabled=True))
    db.add(CompetitorSnapshot(
        store_id=store.id,
        product_id=product.id,
        public_product_id='110676037',
        source='browser_helper',
        status='ok',
        minimum_price=9500,
        offers_json='[{"seller_name":"OTHER","seller_id":"2","price":9500,"delivery_days":0,"position":1}]',
        fetched_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    ))
    db.commit()
    product_id = product.id
    db.close()

    monkeypatch.setattr(incremental_module, 'SessionLocal', Session)
    service = IncrementalPricingService()
    # Avoid background XML task in this unit test; XML behavior has dedicated tests.
    monkeypatch.setattr(service, 'schedule_xml_rebuild', lambda store_id: None)
    result = asyncio.run(service.process_product(product_id, source_device='helper-session:1', http_status=200))
    assert result['ok'] is True
    assert result['status'] == 'changed'
    assert result['new_price'] == 9499

    db = Session()
    saved = db.query(Product).filter_by(id=product_id).one()
    assert saved.current_price == 9499
    job = db.query(AutopilotJob).filter_by(store_id=store.id, mode='incremental').one()
    assert job.processed == 1
    assert job.changed == 1
    db.close()


def test_public_base_never_exposes_localhost(monkeypatch):
    from starlette.requests import Request
    from app.web.router import public_base_info
    from app.core.config import settings

    monkeypatch.setattr(settings, 'PUBLIC_BASE_URL', 'https://kaspi-autoprice.onrender.com')
    request = Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'path': '/', 'raw_path': b'/', 'query_string': b'', 'headers': [(b'host', b'127.0.0.1:8000')], 'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 8000)})
    info = public_base_info(request)
    assert info['is_local'] is True
    assert info['production_base'] == 'https://kaspi-autoprice.onrender.com'
    assert '127.0.0.1' not in info['production_base']


def test_legacy_device_api_is_not_exposed_by_default():
    from app.api.router import api_router

    paths = {route.path for route in api_router.routes}
    assert not any(path.startswith('/local-agent') for path in paths)
    assert not any(path.startswith('/admin') for path in paths)
    assert any(path.startswith('/helper') for path in paths)


def test_browser_helper_requires_explicit_start():
    from pathlib import Path

    js = Path('app/static/js/helper.js').read_text(encoding='utf-8')
    assert "start?.addEventListener('click',run)" in js
    assert 'testCors(task)' in js
    assert 'CORS' in js


def test_restart_requeues_job_owned_by_previous_process(tmp_path, monkeypatch):
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.services.autopilot_service as module
    from app.core.database import Base
    from app.models.autopilot import AutopilotJob, AutopilotJobStatus
    from app.models.store import Store

    engine = create_engine(f'sqlite:///{tmp_path / "recovery.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    store = Store(name='Shop', merchant_id='301')
    db.add(store); db.flush()
    job = AutopilotJob(
        store_id=store.id,
        status=AutopilotJobStatus.RUNNING,
        heartbeat_at=datetime.utcnow(),
        worker_id='old-host:999',
        processed=10,
        total=20,
    )
    db.add(job); db.commit(); job_id = job.id; db.close()

    monkeypatch.setattr(module, 'SessionLocal', Session)
    module.AutoPilotService()._recover_stale_jobs()

    db = Session(); saved = db.query(AutopilotJob).filter_by(id=job_id).one()
    assert saved.status == AutopilotJobStatus.QUEUED
    assert saved.processed == 10
    assert saved.recovery_notice_pending is True
    assert saved.recovery_count == 1
    db.close()
