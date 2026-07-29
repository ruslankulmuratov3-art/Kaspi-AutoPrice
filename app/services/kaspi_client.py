from __future__ import annotations

import json as jsonlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable
from xml.sax.saxutils import escape

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.product import Product
from app.models.store import Store

logger = get_logger(__name__)


class KaspiApiError(RuntimeError):
    """Raised when Kaspi API cannot complete a real request."""


class KaspiApiNotConfigured(KaspiApiError):
    """Raised when API token/base URL is missing. No mock fallback is used."""


class KaspiFeatureNotConfigured(KaspiApiError):
    """Raised when a real endpoint is not configured for a feature."""


@dataclass(slots=True)
class KaspiOffer:
    seller_name: str
    seller_id: str
    price: float
    delivery_days: int
    position: int


@dataclass(slots=True)
class KaspiImportResponse:
    code: str
    status: str | None = None
    description: str | None = None
    raw: Any | None = None


class KaspiClient:
    """
    Real-only Kaspi adapter.

    В этом файле НЕТ random/mock/test данных. Если токен или endpoint не заполнены,
    клиент выбрасывает понятную ошибку, а не делает вид, что всё работает.

    Поддержаны официальные публично описанные product-import методы Kaspi:
    - X-Auth-Token header
    - GET /products/import/schema
    - POST /products/import
    - GET /products/import?i=<code>
    - GET /products/import/result?i=<code>

    Для конкурентных офферов укажи реальный endpoint в KASPI_OFFERS_URL_TEMPLATE,
    если он есть в твоём партнёрском доступе. Без него автодемпинг не будет
    выдумывать цены конкурентов.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.KASPI_BASE_URL).rstrip('/')
        self.timeout = httpx.Timeout(settings.KASPI_HTTP_TIMEOUT_SECONDS)

    def _token(self, store: Store | None = None) -> str:
        token = ''
        if store and store.api_token:
            token = store.api_token.strip()
        if not token:
            token = settings.KASPI_API_TOKEN.strip()
        if not token:
            raise KaspiApiNotConfigured(
                'Kaspi API token не заполнен. Вставь токен в магазин или в .env: KASPI_API_TOKEN=...'
            )
        return token

    def _headers(self, store: Store | None = None, content_type: str | None = None) -> dict[str, str]:
        headers = {
            'Accept': 'application/json',
            'X-Auth-Token': self._token(store),
            'User-Agent': f'{settings.APP_NAME}/{settings.APP_VERSION}',
        }
        if content_type:
            headers['Content-Type'] = content_type
        return headers

    def _url(self, path: str) -> str:
        if path.startswith('http://') or path.startswith('https://'):
            return path
        return f'{self.base_url}/{path.lstrip("/")}'

    async def _request(
        self,
        method: str,
        path: str,
        store: Store | None = None,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        content: str | bytes | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        url = self._url(path)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=json,
                content=content,
                headers=self._headers(store, content_type),
            )
        if response.status_code >= 400:
            body = response.text[:2000]
            raise KaspiApiError(f'Kaspi API error {response.status_code} for {method} {url}: {body}')
        return response

    async def test_connection(self, store: Store | None = None) -> dict[str, Any]:
        """Real request to Kaspi schema endpoint. Does not change products."""
        response = await self._request('GET', settings.KASPI_IMPORT_SCHEMA_PATH, store)
        data = self._safe_json(response)
        return {
            'ok': True,
            'endpoint': self._url(settings.KASPI_IMPORT_SCHEMA_PATH),
            'status_code': response.status_code,
            'schema_title': data.get('title') if isinstance(data, dict) else None,
        }

    async def get_import_schema(self, store: Store | None = None) -> Any:
        response = await self._request('GET', settings.KASPI_IMPORT_SCHEMA_PATH, store)
        return self._safe_json(response)

    async def get_import_status(self, code: str, store: Store | None = None) -> KaspiImportResponse:
        response = await self._request('GET', settings.KASPI_IMPORT_STATUS_PATH, store, params={'i': code})
        data = self._safe_json(response)
        return self._parse_import_response(data)

    async def get_import_result(self, code: str, store: Store | None = None) -> Any:
        response = await self._request('GET', settings.KASPI_IMPORT_RESULT_PATH, store, params={'i': code})
        return self._safe_json(response)

    async def import_products_json(self, products_payload: list[dict[str, Any]], store: Store | None = None) -> KaspiImportResponse:
        if not products_payload:
            raise KaspiApiError('Нельзя отправить пустой список товаров в Kaspi import.')

        # Важно: официальный пример Kaspi для /products/import принимает JSON-тело,
        # но заголовок Content-Type должен быть text/plain, а не application/json.
        # Поэтому НЕ используем параметр json=..., иначе Kaspi отвечает:
        # Content type 'application/json' not supported.
        body = jsonlib.dumps(products_payload, ensure_ascii=False)
        response = await self._request(
            'POST',
            settings.KASPI_PRODUCTS_IMPORT_PATH,
            store,
            content=body.encode('utf-8'),
            content_type='text/plain; charset=utf-8',
        )
        return self._parse_import_response(self._safe_json(response))

    async def import_catalog_xml(self, xml_text: str, store: Store | None = None) -> KaspiImportResponse:
        if not xml_text.strip():
            raise KaspiApiError('Нельзя отправить пустой XML прайс-лист.')
        response = await self._request(
            'POST',
            settings.KASPI_PRODUCTS_IMPORT_PATH,
            store,
            content=xml_text.encode('utf-8'),
            content_type='application/xml; charset=utf-8',
        )
        return self._parse_import_response(self._safe_json(response))

    async def update_price(self, product: Product, store: Store, new_price: float) -> bool:
        """Direct price update through Kaspi API only when an official endpoint is configured.

        Важно: официально задокументированный /products/import возвращает код импорта товара
        и не является подтверждённым прямым endpoint для мгновенного изменения цены одного SKU.
        Поэтому этот метод НЕ имитирует успешное изменение цены через /products/import.

        Основной рабочий способ обновления цен в проекте — полный XML-прайс
        /kaspi-feed/{store_id}.xml, который Kaspi забирает из кабинета продавца.
        """
        if not store:
            raise KaspiApiError('У товара нет магазина.')
        price_int = int(round(float(new_price)))
        if price_int <= 0:
            raise KaspiApiError('Цена должна быть больше 0.')
        if product.min_price and price_int < int(product.min_price):
            raise KaspiApiError(f'Новая цена {price_int} ниже минимальной цены товара {product.min_price}.')
        if product.max_price and product.max_price > 0 and price_int > int(product.max_price):
            raise KaspiApiError(f'Новая цена {price_int} выше максимальной цены товара {product.max_price}.')

        if not bool(getattr(settings, 'KASPI_DIRECT_PRICE_API_ENABLED', False)):
            raise KaspiFeatureNotConfigured(
                'Прямой API изменения цены не включён и не подтверждён. '
                'Используй XML Mode: сайт создаёт полный XML-прайс, а Kaspi забирает его по ссылке.'
            )
        path = str(getattr(settings, 'KASPI_DIRECT_PRICE_UPDATE_PATH', '') or '').strip()
        if not path:
            raise KaspiFeatureNotConfigured(
                'KASPI_DIRECT_PRICE_UPDATE_PATH пустой. Укажи официальный endpoint Kaspi, если он выдан партнёру.'
            )
        method = str(getattr(settings, 'KASPI_DIRECT_PRICE_UPDATE_METHOD', 'POST') or 'POST').upper()
        payload = {
            'sku': product.kaspi_sku,
            'price': price_int,
            'storeId': settings.KASPI_STORE_ID.strip() or store.merchant_id,
            'merchantId': settings.KASPI_MERCHANT_ID.strip() or store.merchant_id,
        }
        response = await self._request(method, path, store, json=payload, content_type='application/json')
        logger.info('Kaspi direct price API requested: sku=%s price=%s status_code=%s',
                    product.kaspi_sku, price_int, response.status_code)
        return True

    async def get_product_offers(self, product: Product, store: Store) -> list[KaspiOffer]:
        """Fetch competitor offers.

        1) Если KASPI_OFFERS_URL_TEMPLATE заполнен — берём оттуда.
        2) Иначе берём публичные предложения с витрины Kaspi через offer-view endpoint.

        Это НЕ заглушка: при ошибке/блокировке вернётся понятная ошибка, а не fake-данные.
        Метод только читает публичные предложения и сам ничего не меняет в Kaspi.
        """
        template = settings.KASPI_OFFERS_URL_TEMPLATE.strip()
        if template:
            url = template.format(
                sku=product.kaspi_sku,
                product_id=self._extract_public_product_id(product),
                merchant_id=store.merchant_id,
                city=store.city,
            )
            response = await self._request('GET', url, store)
            data = self._safe_json(response)
            return self._parse_offers(data)

        if not settings.KASPI_PUBLIC_OFFERS_ENABLED:
            raise KaspiFeatureNotConfigured('Получение публичных предложений выключено: KASPI_PUBLIC_OFFERS_ENABLED=false')
        return await self.get_public_product_offers(product, store)

    def _extract_public_product_id(self, product: Product) -> str:
        """Extract public product id, e.g. 108692468 from URL or SKU 108692468_218383169."""
        text = ' '.join([str(product.url or ''), str(product.kaspi_sku or '')])
        # Kaspi public URLs usually end with ...-108692468/
        m = re.search(r'(?:-|/)(\d{6,})(?:/|$|_)', text)
        if m:
            return m.group(1)
        # fallback: first long number in SKU/url
        m = re.search(r'\d{6,}', text)
        if m:
            return m.group(0)
        raise KaspiApiError('Не смог определить публичный productId Kaspi. Укажи ссылку товара в поле URL или SKU вида 108692468_...')

    async def get_public_product_offers(self, product: Product, store: Store) -> list[KaspiOffer]:
        product_id = self._extract_public_product_id(product)
        url = f"{settings.KASPI_PUBLIC_OFFERS_BASE_URL.rstrip('/')}/{product_id}"
        referer = product.url or f'https://kaspi.kz/shop/p/-{product_id}/'
        payload = {
            'cityId': settings.KASPI_PUBLIC_CITY_ID,
            'id': product_id,
            'page': 0,
            'limit': int(settings.KASPI_PUBLIC_OFFERS_LIMIT),
            'sortOption': settings.KASPI_PUBLIC_SORT_OPTION,
        }
        headers = {
            'Accept': 'application/json, text/plain, */*',
                        'Origin': 'https://kaspi.kz',
            'Referer': referer,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36',
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params=payload, headers=headers)
        if response.status_code >= 400:
            raise KaspiApiError(
                f'Конкуренты временно недоступны: HTTP {response.status_code}. '
                f'Цена будет оставлена без изменений или взята из кэша. Товар: {product_id}. Ответ: {response.text[:500]}'
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise KaspiApiError(f'Публичная витрина Kaspi вернула не JSON: {response.text[:500]}') from exc
        offers = self._parse_offers(data)
        offers = self._remove_own_store_offers(offers, store)
        if not offers:
            raise KaspiApiError(
                'Публичная витрина ответила, но конкуренты не найдены. '
                'Проверь, что productId правильный, город KASPI_PUBLIC_CITY_ID правильный, и на странице есть другие продавцы.'
            )
        return offers

    def _remove_own_store_offers(self, offers: list[KaspiOffer], store: Store) -> list[KaspiOffer]:
        own_names = {
            str(store.name or '').strip().lower(),
            str(settings.KASPI_COMPANY_NAME or '').strip().lower(),
        }
        own_ids = {str(store.merchant_id or '').strip().lower(), str(settings.KASPI_MERCHANT_ID or '').strip().lower()}
        result: list[KaspiOffer] = []
        for offer in offers:
            name = str(offer.seller_name or '').strip().lower()
            sid = str(offer.seller_id or '').strip().lower()
            if name and name in own_names:
                continue
            if sid and sid in own_ids:
                continue
            result.append(offer)
        return result

    def build_product_import_item(self, product: Product, price_int: int, store: Store) -> dict[str, Any]:
        item: dict[str, Any] = {
            'sku': product.kaspi_sku,
            'model': product.name,
            'brand': product.brand or settings.KASPI_DEFAULT_BRAND,
            'price': price_int,
        }
        store_id = settings.KASPI_STORE_ID.strip() or store.merchant_id
        if store_id:
            item['availabilities'] = [
                {
                    'storeId': store_id,
                    'available': 'yes' if product.stock != 0 else 'no',
                    'stockCount': max(int(product.stock or 0), 0),
                }
            ]
        return item

    def build_price_xml(self, product_list: Iterable[Product], prices: dict[str, int], store: Store) -> str:
        company = escape(settings.KASPI_COMPANY_NAME or store.name)
        merchant_id = escape(settings.KASPI_MERCHANT_ID or store.merchant_id)
        store_id = escape(settings.KASPI_STORE_ID or store.merchant_id)
        date = time.strftime('%Y-%m-%d %H:%M')
        offers = []
        for product in product_list:
            price = prices.get(product.kaspi_sku)
            if price is None:
                continue
            available = 'yes' if int(product.stock or 0) != 0 else 'no'
            offers.append(
                f'''    <offer sku="{escape(product.kaspi_sku)}">
      <model>{escape(product.name)}</model>
      <brand>{escape(product.brand or settings.KASPI_DEFAULT_BRAND)}</brand>
      <availabilities>
        <availability available="{available}" storeId="{store_id}" stockCount="{max(int(product.stock or 0), 0)}" />
      </availabilities>
      <price>{int(price)}</price>
    </offer>'''
            )
        if not offers:
            raise KaspiApiError('Нет товаров для XML прайс-листа.')
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<kaspi_catalog xmlns="kaspiShopping" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" date="{escape(date)}" xsi:schemaLocation="kaspiShopping http://kaspi.kz/kaspishopping.xsd">
  <company>{company}</company>
  <merchantid>{merchant_id}</merchantid>
  <offers>
{chr(10).join(offers)}
  </offers>
</kaspi_catalog>
'''

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise KaspiApiError(f'Kaspi вернул не JSON: {response.text[:1000]}') from exc

    def _parse_import_response(self, data: Any) -> KaspiImportResponse:
        if isinstance(data, dict):
            code = str(data.get('code') or data.get('id') or data.get('importCode') or '')
            if not code:
                code = f'local-{uuid.uuid4().hex[:12]}'
            return KaspiImportResponse(
                code=code,
                status=data.get('status'),
                description=data.get('description') or data.get('message'),
                raw=data,
            )
        return KaspiImportResponse(code=f'local-{uuid.uuid4().hex[:12]}', raw=data)

    def _parse_offers(self, data: Any) -> list[KaspiOffer]:
        if isinstance(data, dict):
            rows = (
                data.get('offers')
                or data.get('data')
                or data.get('items')
                or data.get('content')
                or []
            )
        elif isinstance(data, list):
            rows = data
        else:
            rows = []

        offers: list[KaspiOffer] = []
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                continue

            price = self._first_value(item, ['price', 'unitPrice', 'amount', 'value'])
            price_float = self._to_price(price)
            if price_float is None:
                continue

            merchant = item.get('merchant') if isinstance(item.get('merchant'), dict) else {}
            seller = (
                self._first_value(item, ['sellerName', 'merchantName', 'name', 'shopName'])
                or self._first_value(merchant, ['name', 'merchantName', 'title'])
                or 'Продавец'
            )
            seller_id = (
                self._first_value(item, ['sellerId', 'merchantId', 'merchantUid', 'uid', 'id'])
                or self._first_value(merchant, ['id', 'uid', 'merchantId'])
                or ''
            )
            delivery_days = self._to_int(self._first_value(item, ['deliveryDays', 'deliveryDuration', 'delivery', 'days']), 0)
            position = self._to_int(self._first_value(item, ['position', 'rank']), index + 1)

            offers.append(KaspiOffer(
                seller_name=str(seller),
                seller_id=str(seller_id),
                price=price_float,
                delivery_days=delivery_days,
                position=position,
            ))
        return sorted(offers, key=lambda offer: offer.price)

    def _first_value(self, item: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in item and item[key] not in (None, ''):
                return item[key]
        return None

    def _to_price(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value)
        # "11 900 ₸" -> 11900
        cleaned = re.sub(r'[^0-9.,]', '', text).replace(',', '.')
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(float(str(value)))
        except Exception:
            return default


kaspi_client = KaspiClient()
