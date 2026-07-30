from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.product import Product, ProductStatus
from app.models.store import Store
import app.services.xml_feed_service as xml_module


def setup_db(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(xml_module, 'SessionLocal', Session)
    xml_module.xml_feed_service.ROOT = Path(tmp_path) / 'xml'
    xml_module.xml_feed_service.ROOT.mkdir(parents=True, exist_ok=True)
    return Session


def create_catalog(session, count):
    store = Store(name='Shop', merchant_id='30140513')
    session.add(store)
    session.flush()
    products = []
    for index in range(count):
        product = Product(store_id=store.id, kaspi_sku=f'SKU_{index}', product_id=str(100000 + index), name=f'Product {index}', current_price=10000 + index, min_price=9000, max_price=12000, stock=2, status=ProductStatus.ACTIVE, auto_pricing_enabled=True)
        session.add(product)
        products.append(product)
    session.commit()
    return store, products


def test_full_xml_becomes_active(tmp_path, monkeypatch):
    Session = setup_db(tmp_path, monkeypatch)
    db = Session()
    store, products = create_catalog(db, 10)
    record = xml_module.xml_feed_service.save_feed(store=store, products=products, price_by_sku={p.kaspi_sku: int(p.current_price) for p in products})
    assert record['is_active'] is True
    assert record['product_count'] == 10
    assert xml_module.xml_feed_service.get_xml_text(store.id).count('<offer sku=') == 10
    db.close()


def test_partial_xml_is_rejected_and_old_stays_active(tmp_path, monkeypatch):
    Session = setup_db(tmp_path, monkeypatch)
    db = Session()
    store, products = create_catalog(db, 10)
    first = xml_module.xml_feed_service.save_feed(store=store, products=products, price_by_sku={p.kaspi_sku: int(p.current_price) for p in products})
    second = xml_module.xml_feed_service.save_feed(store=store, products=products[:5], price_by_sku={p.kaspi_sku: int(p.current_price) for p in products[:5]})
    assert first['is_active'] is True
    assert second['is_active'] is False
    assert second['status'] == 'rejected'
    assert xml_module.xml_feed_service.get_record(store.id)['feed_id'] == first['feed_id']
    db.close()
