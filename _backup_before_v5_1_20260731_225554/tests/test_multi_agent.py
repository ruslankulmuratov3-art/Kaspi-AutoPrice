import argparse

import httpx

from scripts.local_agent import AgentConfig, agent_headers, submit_result


def config() -> AgentConfig:
    return AgentConfig(
        render_base_url='https://example.test',
        token='secret',
        agent_id='office-pc',
        store_id=1,
        batch_size=25,
        delay_seconds=4.0,
        min_delay_seconds=3.0,
        max_delay_seconds=20.0,
        speedup_after_successes=15,
        poll_seconds=60,
        timeout_seconds=30,
        city_id='750000000',
        offers_base_url='https://kaspi.kz/yml/offer-view/offers',
        offers_limit=10,
        sort_option='PRICE',
        method='POST',
        flush_every=25,
    )


def test_agent_headers_identify_trusted_device():
    headers = agent_headers(config())
    assert headers['X-Agent-Token'] == 'secret'
    assert headers['X-Agent-ID'] == 'office-pc'


def test_agent_result_returns_lease_token():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['body'] = request.read().decode('utf-8')
        captured['agent_id'] = request.headers.get('X-Agent-ID')
        return httpx.Response(200, json={'ok': True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        submit_result(
            client,
            config(),
            {
                'product_id': 7,
                'public_product_id': '110676037',
                'lease_token': 'lease-12345678',
            },
            'ok',
            {'offers': []},
            200,
            '',
            None,
        )
    assert 'lease-12345678' in captured['body']
    assert captured['agent_id'] == 'office-pc'

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.local_agent import AgentResultIn, agent_result, agent_tasks
from app.core.database import Base
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services import competitor_service as competitor_module


def test_two_agents_do_not_receive_same_active_lease(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "multi-agent.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    store = Store(name='Main', merchant_id='301', is_active=True)
    db.add(store)
    db.flush()
    product = Product(
        store_id=store.id,
        kaspi_sku='110676037_1',
        product_id='110676037',
        name='Product',
        current_price=10000,
        min_price=9000,
        max_price=12000,
        stock=1,
        status=ProductStatus.ACTIVE,
        auto_pricing_enabled=True,
    )
    db.add(product)
    db.commit()

    first = agent_tasks(store_id=store.id, limit=10, agent_id='pc-one', db=db)
    second = agent_tasks(store_id=store.id, limit=10, agent_id='phone-two', db=db)
    assert first['count'] == 1
    assert second['count'] == 0

    task = first['items'][0]
    result = agent_result(
        AgentResultIn(
            product_id=product.id,
            public_product_id='110676037',
            lease_token=task['lease_token'],
            status='ok',
            payload={'offers': [{'sellerName': 'Other', 'sellerId': '1', 'price': 9500}]},
            http_status=200,
        ),
        agent_id='pc-one',
        db=db,
    )
    assert result['ok'] is True
    assert result['minimum_price'] == 9500
    db.close()
