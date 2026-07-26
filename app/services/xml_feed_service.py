from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from app.core.config import settings
from app.models.product import Product
from app.models.store import Store


class XmlFeedError(RuntimeError):
    pass


class XmlFeedService:
    """Build and store Kaspi XML price-list feeds.

    This is the official automatic price-list direction: Kaspi pulls a public XML URL.
    We intentionally do not imitate Seller Cabinet clicks and do not use hidden endpoints.
    """

    ROOT = Path('storage/xml_feeds')

    def __init__(self) -> None:
        self.ROOT.mkdir(parents=True, exist_ok=True)

    def build_xml(self, *, store: Store, products: Iterable[Product], price_by_sku: dict[str, int], warehouse_id: str = '') -> str:
        if not store:
            raise XmlFeedError('Магазин не найден.')
        merchant_id = str(getattr(store, 'merchant_id', '') or '').strip()
        company = str(getattr(store, 'name', '') or getattr(settings, 'KASPI_COMPANY_NAME', '') or 'KaspiSeller').strip()
        if not merchant_id:
            raise XmlFeedError('У магазина не указан merchant_id / ID партнёра.')
        warehouse_id = (warehouse_id or '').strip()
        # Warehouse id is required by Kaspi for availabilities. If user doesn't know it yet,
        # we still generate XML with PP1-like fallback so they can see the structure, but the UI warns them.
        if not warehouse_id:
            warehouse_id = 'PP1'
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows: list[str] = []
        rows.append('<?xml version="1.0" encoding="utf-8"?>')
        rows.append(f'<kaspi_catalog date="{escape(now)}" xmlns="kaspiShopping" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="kaspiShopping http://kaspi.kz/kaspishopping.xsd">')
        rows.append(f'  <company>{escape(company)}</company>')
        rows.append(f'  <merchantid>{escape(merchant_id)}</merchantid>')
        rows.append('  <offers>')
        count = 0
        for p in products:
            sku = str(p.kaspi_sku or '').strip()
            if not sku:
                continue
            price = int(price_by_sku.get(sku, round(float(p.current_price or 0))))
            if price <= 0:
                continue
            model = str(p.name or sku).strip()
            brand = str(getattr(p, 'brand', '') or getattr(settings, 'KASPI_DEFAULT_BRAND', '') or 'Без бренда').strip()
            stock = int(getattr(p, 'stock', 0) or 0)
            available = 'yes' if stock != 0 else 'yes'
            stock_attr = f' stockCount="{max(stock, 0)}"' if stock > 0 else ''
            rows.append(f'    <offer sku="{escape(sku)}">')
            rows.append(f'      <model>{escape(model)}</model>')
            rows.append(f'      <brand>{escape(brand)}</brand>')
            rows.append('      <availabilities>')
            rows.append(f'        <availability available="{available}" storeId="{escape(warehouse_id)}"{stock_attr}/>')
            rows.append('      </availabilities>')
            rows.append(f'      <price>{price}</price>')
            rows.append('    </offer>')
            count += 1
        rows.append('  </offers>')
        rows.append('</kaspi_catalog>')
        if count == 0:
            raise XmlFeedError('Нет товаров для XML: проверь SKU и цены.')
        return '\n'.join(rows) + '\n'

    def save_feed(self, *, store: Store, products: Iterable[Product], price_by_sku: dict[str, int], warehouse_id: str = '', processed: int = 0, changed: int = 0, skipped: int = 0, limit_count: int = 0, q_filter: str = '') -> dict:
        xml = self.build_xml(store=store, products=products, price_by_sku=price_by_sku, warehouse_id=warehouse_id)
        store_id = int(store.id)
        filename = f'kaspi_store_{store_id}.xml'
        path = self.ROOT / filename
        path.write_text(xml, encoding='utf-8')
        record = {
            'store_id': store_id,
            'store_name': getattr(store, 'name', ''),
            'merchant_id': getattr(store, 'merchant_id', ''),
            'warehouse_id': warehouse_id or 'PP1',
            'filename': filename,
            'path': str(path),
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'size_bytes': path.stat().st_size,
            'processed': processed,
            'changed': changed,
            'skipped': skipped,
            'limit_count': limit_count,
            'q_filter': q_filter,
        }
        (self.ROOT / f'kaspi_store_{store_id}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return record

    def get_record(self, store_id: int | None) -> dict | None:
        if not store_id:
            return None
        path = self.ROOT / f'kaspi_store_{int(store_id)}.json'
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None

    def file_path_for(self, store_id: int | None) -> Path | None:
        if not store_id:
            return None
        path = self.ROOT / f'kaspi_store_{int(store_id)}.xml'
        return path if path.exists() else None


xml_feed_service = XmlFeedService()
