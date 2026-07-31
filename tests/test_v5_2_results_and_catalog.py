from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.autopilot import AutopilotJob, AutopilotJobItem, AutopilotJobStatus, CompetitorSnapshot
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.result_view_service import ResultViewService
from app.web.router import web_router


def make_session(tmp_path, name='v52.db'):
    engine = create_engine(
        f'sqlite:///{tmp_path / name}',
        connect_args={'check_same_thread': False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_historical_unavailable_row_shows_current_competitors(tmp_path):
    Session = make_session(tmp_path)
    db = Session()
    store = Store(name='OWN', merchant_id='301')
    db.add(store)
    db.flush()
    product = Product(
        store_id=store.id,
        kaspi_sku='SKU-1',
        product_id='1001',
        name='Product',
        current_price=10000,
        min_price=8000,
        max_price=12000,
        status=ProductStatus.ACTIVE,
        auto_pricing_enabled=True,
    )
    db.add(product)
    db.flush()
    db.add(PricingRule(
        product_id=product.id,
        strategy=PricingStrategy.BEAT_BY_STEP,
        beat_step=1,
        min_margin_percent=0,
        max_change_percent_per_run=50,
        is_enabled=True,
    ))
    job = AutopilotJob(store_id=store.id, status=AutopilotJobStatus.DONE, total=1, processed=1)
    db.add(job)
    db.flush()
    old_time = datetime.utcnow() - timedelta(minutes=10)
    row = AutopilotJobItem(
        job_id=job.id,
        store_id=store.id,
        product_id=product.id,
        sku=product.kaspi_sku,
        product_name=product.name,
        old_price=10000,
        new_price=10000,
        status='safe_skipped',
        reason='Конкуренты временно недоступны. Цена оставлена без изменений.',
        data_source='unavailable',
        cache_state='missing',
        updated_at=old_time,
    )
    db.add(row)
    db.add(CompetitorSnapshot(
        store_id=store.id,
        product_id=product.id,
        public_product_id='1001',
        source='browser_helper',
        status='ok',
        minimum_price=9500,
        offers_json='[{"seller_name":"OTHER","seller_id":"2","price":9500,"delivery_days":0,"position":1}]',
        fetched_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    ))
    db.commit()

    view = ResultViewService().build(db, [row])[0]
    assert view['historical_unavailable'] is True
    assert view['current_available'] is True
    assert view['current_is_newer'] is True
    assert view['current_min_price'] == 9500
    assert view['current_source_label'] == 'Телефон-помощник'
    assert view['can_recalculate'] is True
    assert 'Сейчас данные уже получены' in view['display_reason']
    db.close()


def test_catalog_management_routes_are_registered():
    paths = {(route.path, tuple(sorted(route.methods or []))) for route in web_router.routes}
    assert any(path == '/products/bulk-action' and 'POST' in methods for path, methods in paths)
    assert any(path == '/products/{product_id}/archive' and 'POST' in methods for path, methods in paths)
    assert any(path == '/products/{product_id}/restore' and 'POST' in methods for path, methods in paths)
    assert any(path == '/products/{product_id}/recalculate' and 'POST' in methods for path, methods in paths)


def test_product_page_has_select_all_delete_and_add_controls():
    template = Path('app/templates/products.html').read_text(encoding='utf-8')
    js = Path('app/static/js/app.js').read_text(encoding='utf-8')
    assert 'data-select-all' in template
    assert '/products/bulk-action' in template
    assert 'Добавить товар' in template
    assert 'new Set' in js
    assert 'data-requires-selection' in js
    assert 'dataset.confirm' in js


def test_archived_product_is_not_active():
    product = Product(
        store_id=1,
        kaspi_sku='SKU',
        name='Product',
        status=ProductStatus.ARCHIVED,
        auto_pricing_enabled=False,
    )
    assert product.status == ProductStatus.ARCHIVED
    assert product.auto_pricing_enabled is False
