from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.autopilot import AutopilotJob, AutopilotJobStatus
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.autopilot_service import AutoPilotService
import app.services.autopilot_service as autopilot_module
import app.services.xml_feed_service as xml_module


def make_session(tmp_path, name='v51.db'):
    engine = create_engine(
        f'sqlite:///{tmp_path / name}',
        connect_args={'check_same_thread': False, 'timeout': 10},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_xml_uses_same_session_during_job_finalization(tmp_path, monkeypatch):
    Session = make_session(tmp_path)
    monkeypatch.setattr(xml_module, 'SessionLocal', Session)
    xml_module.xml_feed_service.ROOT = Path(tmp_path) / 'xml'
    xml_module.xml_feed_service.ROOT.mkdir(parents=True, exist_ok=True)

    db = Session()
    store = Store(name='Shop', merchant_id='30140513')
    db.add(store)
    db.flush()
    product = Product(
        store_id=store.id,
        kaspi_sku='SKU_1',
        product_id='1001',
        name='Product',
        current_price=10000,
        min_price=9000,
        max_price=12000,
        stock=2,
        status=ProductStatus.ACTIVE,
        auto_pricing_enabled=True,
    )
    db.add(product)
    db.commit()

    # Hold an active SQLite write transaction exactly like the autopilot limiter/finalizer.
    product.current_price = 9999
    db.add(product)
    db.flush()

    record = xml_module.xml_feed_service.save_feed(
        store=store,
        products=[product],
        price_by_sku={'SKU_1': 9999},
        db=db,
        commit=False,
        write_file=False,
        changed=1,
    )
    db.commit()

    assert record['is_active'] is True
    assert xml_module.xml_feed_service.get_xml_text(store.id).count('<offer sku=') == 1
    db.close()


def test_status_contains_remaining_and_eta(tmp_path, monkeypatch):
    Session = make_session(tmp_path, 'eta.db')
    db = Session()
    store = Store(name='Shop', merchant_id='301')
    db.add(store)
    db.flush()
    job = AutopilotJob(
        store_id=store.id,
        status=AutopilotJobStatus.RUNNING,
        mode='local_agent_sync',
        total=100,
        processed=25,
        started_at=datetime.utcnow(),
        heartbeat_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    store_id = store.id
    db.close()

    monkeypatch.setattr(autopilot_module, 'SessionLocal', Session)
    status = AutoPilotService().last_status(store_id)
    assert status['remaining'] == 75
    assert status['eta_seconds'] is not None
    assert status['eta_seconds'] > 0


def test_friendly_error_hides_sql_and_xml_payload():
    error = RuntimeError(
        '(sqlite3.OperationalError) database is locked '
        '[SQL: INSERT INTO xml_feed_versions ...] [parameters: huge xml]'
    )
    message = AutoPilotService._friendly_error(error)
    assert 'database is locked' not in message.casefold()
    assert '[SQL:' not in message
    assert len(message) < 500
