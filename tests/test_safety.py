import asyncio

import pytest

from app.models.product import Product
from app.models.store import Store
from app.services.kaspi_client import KaspiFeatureNotConfigured, kaspi_client
from app.services.search_service import product_text_matches


def test_direct_price_api_disabled():
    product = Product(kaspi_sku='1', name='x', current_price=100)
    store = Store(name='s', merchant_id='m')
    with pytest.raises(KaspiFeatureNotConfigured):
        asyncio.run(kaspi_client.update_price(product, store, 100))


def test_search_is_casefold_and_partial():
    product = Product(kaspi_sku='108692468_1', product_id='108692468', name='Комплект постельного белья Kafuman')
    assert product_text_matches(product, 'ПОСТЕЛЬ')
    assert product_text_matches(product, 'kafuman')
    assert product_text_matches(product, '108692468')
