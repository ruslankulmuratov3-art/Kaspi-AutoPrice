from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.autopilot import AutopilotJob, AutopilotJobItem, AutopilotJobStatus
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.autopilot_service import AutoPilotService


def test_job_items_survive_pause_resume(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "autopilot.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    store = Store(name='Shop', merchant_id='301')
    db.add(store)
    db.flush()
    product = Product(
        store_id=store.id,
        kaspi_sku='SKU_1',
        product_id='1',
        name='Product',
        current_price=10000,
        min_price=9000,
        max_price=12000,
        stock=1,
        status=ProductStatus.ACTIVE,
        auto_pricing_enabled=True,
    )
    db.add(product)
    db.flush()
    job = AutopilotJob(store_id=store.id, status=AutopilotJobStatus.PAUSED, processed=1, total=2, cursor_product_id=product.id)
    db.add(job)
    db.flush()

    service = AutoPilotService()
    service._save_job_item(db, job, {
        'product_id': product.id,
        'sku': product.kaspi_sku,
        'name': product.name,
        'old_price': 10000,
        'new_price': 9999,
        'changed': True,
        'status': 'changed',
        'reason': 'ниже конкурента',
        'data_source': 'cache',
        'cache_state': 'fresh',
    })
    db.commit()

    saved = db.query(AutopilotJobItem).filter_by(job_id=job.id, product_id=product.id).one()
    assert saved.new_price == 9999
    decisions = service._job_decisions(db, job.id)
    assert decisions[0]['changed'] is True
    assert decisions[0]['new_price'] == 9999

    resumed = service.resume(db, job.id)
    assert resumed.status == AutopilotJobStatus.QUEUED
    assert resumed.cursor_product_id == product.id
    assert db.query(AutopilotJobItem).filter_by(job_id=job.id).count() == 1
    db.close()
