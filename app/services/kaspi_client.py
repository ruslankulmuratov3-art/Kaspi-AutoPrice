from __future__ import annotations

import json as jsonlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.product import Product
from app.models.store import Store

logger = get_logger(__name__)


class KaspiApiError(RuntimeError):
    """Base error for official and public Kaspi requests."""


class KaspiApiNotConfigured(KaspiApiError):
    pass


class KaspiFeatureNotConfigured(KaspiApiError):
    pass


class KaspiHttpError(KaspiApiError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: int | None = None, body_excerpt: str = ''):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.body_excerpt = body_excerpt


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
    """Kaspi adapter with an explicit boundary between official API and public offers.

    Official API token is used only for documented product-import/schema/status calls.
    Direct price mutation stays disabled unless the seller receives a confirmed endpoint.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.KASPI_BASE_URL).rstrip('/')
        self.timeout = httpx.Timeout(settings.KASPI_HTTP_TIMEOUT_SECONDS)

    def _token(self, store: Store | None = None) -> str:
        token = (store.api_token or '').strip() if store else ''
        token = token or settings.KASPI_API_TOKEN.strip()
        if not token:
            raise KaspiApiNotConfigured('Kaspi API token не заполнен.')
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
        if path.startswith(('http://', 'https://')):
            return path
        return f'{self.base_url}/{path.lstrip("/")}'

    @staticmethod
    def _safe_excerpt(text: str, limit: int | None = None) -> str:
        limit = int(limit or settings.KASPI_PUBLIC_OFFERS_HTML_MAX_CHARS or 240)
        compact = re.sub(r'\s+', ' ', str(text or '')).strip()
        return compact[:limit]

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get('Retry-After', '').strip()
        try:
            return max(1, int(float(raw))) if raw else None
        except ValueError:
            return None

    async def _request(self, method: str, path: str, store: Store | None = None, *, params=None, json=None, content=None, content_type=None) -> httpx.Response:
        url = self._url(path)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(method, url, params=params, json=json, content=content, headers=self._headers(store, content_type))
        if response.status_code >= 400:
            excerpt = self._safe_excerpt(response.text, 500)
            raise KaspiHttpError(
                f'Kaspi API: HTTP {response.status_code}',
                status_code=response.status_code,
                retry_after=self._retry_after(response),
                body_excerpt=excerpt,
            )
        return response

    async def test_connection(self, store: Store | None = None) -> dict[str, Any]:
        response = await self._request('GET', settings.KASPI_IMPORT_SCHEMA_PATH, store)
        data = self._safe_json(response)
        return {'ok': True, 'endpoint': self._url(settings.KASPI_IMPORT_SCHEMA_PATH), 'status_code': response.status_code, 'schema_title': data.get('title') if isinstance(data, dict) else None}

    async def get_import_schema(self, store: Store | None = None) -> Any:
        return self._safe_json(await self._request('GET', settings.KASPI_IMPORT_SCHEMA_PATH, store))

    async def get_import_status(self, code: str, store: Store | None = None) -> KaspiImportResponse:
        return self._parse_import_response(self._safe_json(await self._request('GET', settings.KASPI_IMPORT_STATUS_PATH, store, params={'i': code})))

    async def get_import_result(self, code: str, store: Store | None = None) -> Any:
        return self._safe_json(await self._request('GET', settings.KASPI_IMPORT_RESULT_PATH, store, params={'i': code}))

    async def import_products_json(self, products_payload: list[dict[str, Any]], store: Store | None = None) -> KaspiImportResponse:
        if not products_payload:
            raise KaspiApiError('Нельзя отправить пустой список товаров.')
        body = jsonlib.dumps(products_payload, ensure_ascii=False)
        response = await self._request('POST', settings.KASPI_PRODUCTS_IMPORT_PATH, store, content=body.encode('utf-8'), content_type='text/plain; charset=utf-8')
        return self._parse_import_response(self._safe_json(response))

    async def update_price(self, product: Product, store: Store, new_price: float) -> bool:
        if not settings.KASPI_DIRECT_PRICE_API_ENABLED or not settings.KASPI_DIRECT_PRICE_UPDATE_PATH.strip():
            raise KaspiFeatureNotConfigured('Прямой API изменения цены не подтверждён. Используйте полный XML-прайс.')
        price_int = int(round(float(new_price)))
        if price_int <= 0:
            raise KaspiApiError('Цена должна быть больше 0.')
        payload = {'sku': product.kaspi_sku, 'price': price_int, 'merchantId': store.merchant_id}
        method = settings.KASPI_DIRECT_PRICE_UPDATE_METHOD.strip().upper() or 'POST'
        await self._request(method, settings.KASPI_DIRECT_PRICE_UPDATE_PATH, store, json=payload, content_type='application/json')
        return True

    def extract_public_product_id(self, product: Product) -> str:
        explicit = str(getattr(product, 'product_id', '') or '').strip()
        if explicit.isdigit() and len(explicit) >= 6:
            return explicit
        text = ' '.join([str(product.url or ''), str(product.kaspi_sku or '')])
        match = re.search(r'(?:-|/)(\d{6,})(?:/|$|_)', text) or re.search(r'\d{6,}', text)
        if not match:
            raise KaspiApiError('Не удалось определить product_id Kaspi.')
        return match.group(1) if match.lastindex else match.group(0)

    async def get_product_offers(self, product: Product, store: Store) -> list[KaspiOffer]:
        template = settings.KASPI_OFFERS_URL_TEMPLATE.strip()
        if template:
            url = template.format(sku=product.kaspi_sku, product_id=self.extract_public_product_id(product), merchant_id=store.merchant_id, city=store.city)
            data = self._safe_json(await self._request('GET', url, store))
            return self._remove_own_store_offers(self._parse_offers(data), store)
        if not settings.KASPI_PUBLIC_OFFERS_ENABLED:
            raise KaspiFeatureNotConfigured('Получение конкурентов выключено.')
        return await self.get_public_product_offers(product, store)

    async def get_public_product_offers(self, product: Product, store: Store) -> list[KaspiOffer]:
        product_id = self.extract_public_product_id(product)
        url = f"{settings.KASPI_PUBLIC_OFFERS_BASE_URL.rstrip('/')}/{product_id}"
        payload = {
            'cityId': settings.KASPI_PUBLIC_CITY_ID,
            'id': product_id,
            'page': 0,
            'limit': int(settings.KASPI_PUBLIC_OFFERS_LIMIT),
            'sortOption': settings.KASPI_PUBLIC_SORT_OPTION,
        }
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Origin': 'https://kaspi.kz',
            'Referer': product.url or f'https://kaspi.kz/shop/p/-{product_id}/',
            'User-Agent': f'{settings.APP_NAME}/{settings.APP_VERSION}',
        }
        method = settings.KASPI_PUBLIC_OFFERS_METHOD.strip().upper() or 'POST'
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            if method == 'GET':
                response = await client.get(url, params=payload, headers=headers)
            else:
                response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            excerpt = self._safe_excerpt(response.text)
            raise KaspiHttpError(
                f'Конкуренты временно недоступны: HTTP {response.status_code}',
                status_code=response.status_code,
                retry_after=self._retry_after(response),
                body_excerpt=excerpt,
            )
        content_type = response.headers.get('content-type', '').lower()
        if 'json' not in content_type and response.text.lstrip().startswith('<'):
            raise KaspiHttpError('Вместо JSON получена HTML-страница', status_code=response.status_code, body_excerpt=self._safe_excerpt(response.text))
        try:
            data = response.json()
        except ValueError as exc:
            raise KaspiHttpError('Kaspi вернул некорректный ответ', status_code=response.status_code, body_excerpt=self._safe_excerpt(response.text)) from exc
        offers = self._remove_own_store_offers(self._parse_offers(data), store)
        if not offers:
            raise KaspiApiError('Конкуренты не найдены.')
        return offers

    def _remove_own_store_offers(self, offers: list[KaspiOffer], store: Store) -> list[KaspiOffer]:
        own_names = {str(store.name or '').strip().casefold(), str(settings.KASPI_COMPANY_NAME or '').strip().casefold()}
        own_ids = {str(store.merchant_id or '').strip().casefold(), str(settings.KASPI_MERCHANT_ID or '').strip().casefold()}
        return [o for o in offers if str(o.seller_name or '').strip().casefold() not in own_names and str(o.seller_id or '').strip().casefold() not in own_ids]

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise KaspiApiError(f'Kaspi вернул не JSON: {self._safe_excerpt(response.text, 500)}') from exc

    def _parse_import_response(self, data: Any) -> KaspiImportResponse:
        if isinstance(data, dict):
            code = str(data.get('code') or data.get('id') or data.get('importCode') or f'local-{uuid.uuid4().hex[:12]}')
            return KaspiImportResponse(code=code, status=data.get('status'), description=data.get('description') or data.get('message'), raw=data)
        return KaspiImportResponse(code=f'local-{uuid.uuid4().hex[:12]}', raw=data)

    def parse_offers_payload(self, data: Any) -> list[KaspiOffer]:
        """Normalize a public-offers JSON payload received by the trusted local agent."""
        return self._parse_offers(data)

    def _parse_offers(self, data: Any) -> list[KaspiOffer]:
        rows = []
        if isinstance(data, dict):
            rows = data.get('offers') or data.get('data') or data.get('items') or data.get('content') or []
        elif isinstance(data, list):
            rows = data
        offers: list[KaspiOffer] = []
        for index, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            merchant = item.get('merchant') if isinstance(item.get('merchant'), dict) else {}
            price = self._to_price(self._first_value(item, ['price', 'unitPrice', 'amount', 'value']))
            if price is None or price <= 0:
                continue
            seller = self._first_value(item, ['sellerName', 'merchantName', 'name', 'shopName']) or self._first_value(merchant, ['name', 'merchantName', 'title']) or 'Продавец'
            seller_id = self._first_value(item, ['sellerId', 'merchantId', 'merchantUid', 'uid', 'id']) or self._first_value(merchant, ['id', 'uid', 'merchantId']) or ''
            offers.append(KaspiOffer(str(seller), str(seller_id), price, self._to_int(self._first_value(item, ['deliveryDays', 'deliveryDuration', 'delivery', 'days'])), self._to_int(self._first_value(item, ['position', 'rank']), index + 1)))
        return sorted(offers, key=lambda offer: offer.price)

    @staticmethod
    def _first_value(item: dict[str, Any], keys: list[str]) -> Any:
        for key in keys:
            if key in item and item[key] not in (None, ''):
                return item[key]
        return None

    @staticmethod
    def _to_price(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        cleaned = re.sub(r'[^0-9.,]', '', str(value)).replace(',', '.')
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(str(value)))
        except Exception:
            return default


kaspi_client = KaspiClient()
