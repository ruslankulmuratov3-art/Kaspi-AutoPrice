import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.competitor_service import CompetitorService, CompetitorUnavailable
from app.services.kaspi_client import KaspiHttpError
import app.services.competitor_service as competitor_module


def test_429_opens_circuit_and_stops_repeat_requests(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "competitors.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    store = Store(name='Shop', merchant_id='301')
    db.add(store); db.flush()
    product = Product(store_id=store.id, kaspi_sku='110676037_1', product_id='110676037', name='Product', current_price=10000, min_price=9000, max_price=12000, stock=1, status=ProductStatus.ACTIVE)
    db.add(product); db.commit(); db.refresh(product)
    calls = {'count': 0}

    async def blocked(*args, **kwargs):
        calls['count'] += 1
        raise KaspiHttpError('HTTP 429', status_code=429, retry_after=1800)

    monkeypatch.setattr(competitor_module.kaspi_client, 'get_product_offers', blocked)
    service = CompetitorService()
    try:
        asyncio.run(service.get(db, product, force=True))
    except CompetitorUnavailable:
        pass
    else:
        raise AssertionError('Expected CompetitorUnavailable')
    assert calls['count'] == 1
    try:
        asyncio.run(service.get(db, product, force=True))
    except CompetitorUnavailable:
        pass
    assert calls['count'] == 1
    assert service.state_info(db)['state'] == 'open'
    db.close()
