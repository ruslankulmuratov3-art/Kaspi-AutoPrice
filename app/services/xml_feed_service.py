from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, Any
from xml.sax.saxutils import escape

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.product import Product
from app.models.store import Store
from app.models.task_log import TaskLog, TaskStatus


class XmlFeedError(RuntimeError):
    pass


class XmlFeedService:
    """Build, store and audit Kaspi XML feeds.

    XML and audit are saved both to local storage and PostgreSQL(TaskLog). Local files can vanish on
    Render redeploy; PostgreSQL records stay when DATABASE_URL is configured.
    """

    ROOT = Path('storage/xml_feeds')
    VERSIONS_DIR = ROOT / 'versions'

    def __init__(self) -> None:
        self.ROOT.mkdir(parents=True, exist_ok=True)
        self.VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def _now(self) -> str:
        return datetime.utcnow().isoformat(timespec='seconds')

    def _feed_id(self) -> str:
        return datetime.utcnow().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def _versions_index_path(self, store_id: int) -> Path:
        return self.ROOT / f'kaspi_store_{int(store_id)}_versions.json'

    def _pulls_index_path(self, store_id: int) -> Path:
        return self.ROOT / f'kaspi_store_{int(store_id)}_pulls.json'

    def _version_path(self, store_id: int, feed_id: str) -> Path:
        return self.VERSIONS_DIR / f'kaspi_store_{int(store_id)}_{feed_id}.json'

    def _save_task(self, task_name: str, task_id: str, payload: dict, message: str = '') -> None:
        try:
            db = SessionLocal()
            db.add(TaskLog(
                task_name=task_name,
                task_id=str(task_id),
                status=TaskStatus.SUCCESS,
                message=message[:1000],
                payload_json=json.dumps(payload, ensure_ascii=False),
            ))
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    def build_xml(self, *, store: Store, products: Iterable[Product], price_by_sku: dict[str, int], warehouse_id: str = '') -> str:
        if not store:
            raise XmlFeedError('Магазин не найден.')
        merchant_id = str(getattr(store, 'merchant_id', '') or '').strip() or str(settings.KASPI_MERCHANT_ID or '').strip()
        company = str(getattr(store, 'name', '') or getattr(settings, 'KASPI_COMPANY_NAME', '') or 'KaspiSeller').strip()
        if not merchant_id:
            raise XmlFeedError('У магазина не указан merchant_id / ID партнёра.')
        warehouse_id = (warehouse_id or '').strip() or str(settings.KASPI_AUTOPILOT_WAREHOUSE_ID or settings.KASPI_STORE_ID or 'PP1').strip()

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
            model = str(getattr(p, 'model', '') or p.name or sku).strip()
            brand = str(getattr(p, 'brand', '') or getattr(settings, 'KASPI_DEFAULT_BRAND', '') or 'NoBrand').strip()
            stock = int(getattr(p, 'stock', 0) or 0)
            stock_attr = f' stockCount="{max(stock, 0)}"' if stock > 0 else ''
            rows.append(f'    <offer sku="{escape(sku)}">')
            rows.append(f'      <model>{escape(model)}</model>')
            rows.append(f'      <brand>{escape(brand)}</brand>')
            rows.append('      <availabilities>')
            rows.append(f'        <availability available="yes" storeId="{escape(warehouse_id)}"{stock_attr}/>')
            rows.append('      </availabilities>')
            rows.append(f'      <price>{price}</price>')
            rows.append('    </offer>')
            count += 1
        rows.append('  </offers>')
        rows.append('</kaspi_catalog>')
        if count == 0:
            raise XmlFeedError('Нет товаров для XML: проверь SKU и цены.')
        return '\n'.join(rows) + '\n'

    def save_feed(
        self,
        *,
        store: Store,
        products: Iterable[Product],
        price_by_sku: dict[str, int],
        warehouse_id: str = '',
        processed: int = 0,
        changed: int = 0,
        skipped: int = 0,
        limit_count: int = 0,
        q_filter: str = '',
        details: list[dict] | None = None,
    ) -> dict:
        products_list = list(products)
        xml = self.build_xml(store=store, products=products_list, price_by_sku=price_by_sku, warehouse_id=warehouse_id)
        store_id = int(store.id)
        feed_id = self._feed_id()
        filename = f'kaspi_store_{store_id}.xml'
        path = self.ROOT / filename
        path.write_text(xml, encoding='utf-8')
        size_bytes = path.stat().st_size
        updated_at = self._now()

        safe_details = details or []
        if not safe_details:
            for p in products_list:
                sku = str(p.kaspi_sku or '').strip()
                old_price = int(round(float(p.current_price or 0)))
                new_price = int(price_by_sku.get(sku, old_price))
                safe_details.append({
                    'product_id': getattr(p, 'id', None),
                    'sku': sku,
                    'name': str(getattr(p, 'name', '') or sku),
                    'old_price': old_price,
                    'new_price': new_price,
                    'delta': new_price - old_price,
                    'changed': new_price != old_price,
                    'reason': 'XML snapshot',
                    'status': 'changed' if new_price != old_price else 'same',
                })

        record = {
            'feed_id': feed_id,
            'store_id': store_id,
            'store_name': getattr(store, 'name', ''),
            'merchant_id': getattr(store, 'merchant_id', ''),
            'warehouse_id': warehouse_id or 'PP1',
            'filename': filename,
            'path': str(path),
            'updated_at': updated_at,
            'size_bytes': size_bytes,
            'processed': int(processed or len(products_list)),
            'changed': int(changed or 0),
            'skipped': int(skipped or 0),
            'limit_count': int(limit_count or 0),
            'q_filter': q_filter or '',
            'details_count': len(safe_details),
            'type': 'XML',
            'status': 'ready',
        }
        full_snapshot = {**record, 'details': safe_details, 'xml_text': xml}
        self._write_json(self.ROOT / f'kaspi_store_{store_id}.json', record)
        self._write_json(self._version_path(store_id, feed_id), full_snapshot)
        versions = self._read_json(self._versions_index_path(store_id), [])
        versions.insert(0, record)
        self._write_json(self._versions_index_path(store_id), versions[:200])
        self._save_task('xml_feed_version', feed_id, full_snapshot, f'{record["store_name"]}: XML {record["processed"]} товаров')
        return record

    def _db_versions(self, store_id: int | None, limit: int = 20) -> list[dict]:
        if not store_id:
            return []
        rows: list[dict] = []
        try:
            db = SessionLocal()
            logs = db.query(TaskLog).filter(TaskLog.task_name == 'xml_feed_version').order_by(TaskLog.created_at.desc()).limit(max(100, limit * 5)).all()
            for log in logs:
                try:
                    payload = json.loads(log.payload_json or '{}')
                except Exception:
                    continue
                if int(payload.get('store_id') or 0) != int(store_id):
                    continue
                slim = {k: v for k, v in payload.items() if k not in ('details', 'xml_text')}
                rows.append(slim)
                if len(rows) >= limit:
                    break
        except Exception:
            rows = []
        finally:
            try:
                db.close()
            except Exception:
                pass
        return rows

    def get_record(self, store_id: int | None) -> dict | None:
        versions = self._db_versions(store_id, 1)
        if versions:
            return versions[0]
        if not store_id:
            return None
        return self._read_json(self.ROOT / f'kaspi_store_{int(store_id)}.json', None)

    def list_versions(self, store_id: int | None, limit: int = 20) -> list[dict]:
        versions = self._db_versions(store_id, limit)
        if versions:
            return versions
        if not store_id:
            return []
        versions = self._read_json(self._versions_index_path(int(store_id)), [])
        return list(versions or [])[:limit]

    def get_version(self, store_id: int | None, feed_id: str | None) -> dict | None:
        if not store_id or not feed_id:
            return None
        try:
            db = SessionLocal()
            log = db.query(TaskLog).filter(TaskLog.task_name == 'xml_feed_version', TaskLog.task_id == str(feed_id)).order_by(TaskLog.id.desc()).first()
            if log:
                payload = json.loads(log.payload_json or '{}')
                if int(payload.get('store_id') or 0) == int(store_id):
                    return payload
        except Exception:
            pass
        finally:
            try:
                db.close()
            except Exception:
                pass
        return self._read_json(self._version_path(int(store_id), str(feed_id)), None)

    def get_xml_text(self, store_id: int | None) -> str | None:
        record = self.get_record(store_id)
        if not record:
            return None
        path = self.file_path_for(store_id)
        if path and path.exists():
            return path.read_text(encoding='utf-8')
        version = self.get_version(store_id, record.get('feed_id'))
        if version:
            return version.get('xml_text')
        return None

    def log_pull(self, store_id: int | None, request=None) -> dict | None:
        if not store_id:
            return None
        store_id = int(store_id)
        record = self.get_record(store_id) or {}
        headers = getattr(request, 'headers', {}) or {}
        client = getattr(request, 'client', None)
        x_forwarded_for = headers.get('x-forwarded-for', '') if hasattr(headers, 'get') else ''
        user_agent = headers.get('user-agent', '') if hasattr(headers, 'get') else ''
        remote_ip = (x_forwarded_for.split(',')[0].strip() if x_forwarded_for else '') or (getattr(client, 'host', '') if client else '')
        pull = {
            'pull_id': self._feed_id(),
            'store_id': store_id,
            'accessed_at': self._now(),
            'ip': remote_ip,
            'user_agent': user_agent,
            'path': str(getattr(getattr(request, 'url', None), 'path', '') or ''),
            'feed_id': record.get('feed_id'),
            'feed_updated_at': record.get('updated_at'),
            'filename': record.get('filename'),
            'size_bytes': record.get('size_bytes', 0),
            'processed': record.get('processed', 0),
            'changed': record.get('changed', 0),
            'skipped': record.get('skipped', 0),
            'likely_kaspi': 'kaspi' in str(user_agent).lower(),
        }
        pulls = self._read_json(self._pulls_index_path(store_id), [])
        pulls.insert(0, pull)
        self._write_json(self._pulls_index_path(store_id), pulls[:500])
        self._save_task('xml_feed_pull', pull['pull_id'], pull, 'XML requested')
        return pull

    def list_pulls(self, store_id: int | None, limit: int = 50) -> list[dict]:
        if not store_id:
            return []
        rows: list[dict] = []
        try:
            db = SessionLocal()
            logs = db.query(TaskLog).filter(TaskLog.task_name == 'xml_feed_pull').order_by(TaskLog.created_at.desc()).limit(max(100, limit * 5)).all()
            for log in logs:
                try:
                    payload = json.loads(log.payload_json or '{}')
                except Exception:
                    continue
                if int(payload.get('store_id') or 0) != int(store_id):
                    continue
                rows.append(payload)
                if len(rows) >= limit:
                    break
        except Exception:
            rows = []
        finally:
            try:
                db.close()
            except Exception:
                pass
        if rows:
            return rows
        pulls = self._read_json(self._pulls_index_path(int(store_id)), [])
        return list(pulls or [])[:limit]

    def file_path_for(self, store_id: int | None) -> Path | None:
        if not store_id:
            return None
        path = self.ROOT / f'kaspi_store_{int(store_id)}.xml'
        return path if path.exists() else None


xml_feed_service = XmlFeedService()
