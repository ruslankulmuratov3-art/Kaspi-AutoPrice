from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.models.xml_feed import XmlFeedPull, XmlFeedStatus, XmlFeedVersion


class XmlFeedError(RuntimeError):
    pass


class XmlFeedService:
    ROOT = Path('storage/xml_feeds')

    def __init__(self) -> None:
        self.ROOT.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec='seconds')

    @staticmethod
    def _feed_id() -> str:
        return datetime.utcnow().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]

    @staticmethod
    def _details(value: str | None) -> list[dict[str, Any]]:
        try:
            data = json.loads(value or '[]')
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def active_version(self, db: Session, store_id: int) -> XmlFeedVersion | None:
        return (
            db.query(XmlFeedVersion)
            .filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.is_active == True)
            .order_by(XmlFeedVersion.id.desc())
            .first()
        )

    def expected_products(self, db: Session, store_id: int) -> list[Product]:
        return (
            db.query(Product)
            .filter(
                Product.store_id == int(store_id),
                Product.status == ProductStatus.ACTIVE,
                Product.current_price > 0,
                Product.kaspi_sku != '',
            )
            .order_by(Product.id.asc())
            .all()
        )

    def build_xml(self, *, store: Store, products: Iterable[Product], price_by_sku: dict[str, int], warehouse_id: str = '') -> tuple[str, int]:
        if not store:
            raise XmlFeedError('Магазин не найден.')
        merchant_id = str(store.merchant_id or settings.KASPI_MERCHANT_ID or '').strip()
        company = str(store.name or settings.KASPI_COMPANY_NAME or 'KaspiSeller').strip()
        if not merchant_id:
            raise XmlFeedError('У магазина не указан merchant_id.')
        warehouse_id = (warehouse_id or settings.KASPI_AUTOPILOT_WAREHOUSE_ID or settings.KASPI_STORE_ID or 'PP1').strip()
        if not warehouse_id:
            raise XmlFeedError('Не указан склад storeId.')

        rows = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<kaspi_catalog date="{escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}" xmlns="kaspiShopping" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="kaspiShopping http://kaspi.kz/kaspishopping.xsd">',
            f'  <company>{escape(company)}</company>',
            f'  <merchantid>{escape(merchant_id)}</merchantid>',
            '  <offers>',
        ]
        count = 0
        seen_skus: set[str] = set()
        invalid: list[str] = []
        for product in products:
            sku = str(product.kaspi_sku or '').strip()
            if not sku:
                invalid.append(f'product:{product.id}:empty-sku')
                continue
            if sku in seen_skus:
                raise XmlFeedError(f'Дублирующийся SKU в XML: {sku}')
            seen_skus.add(sku)
            price = int(price_by_sku.get(sku, round(float(product.current_price or 0))))
            if price <= 0:
                raise XmlFeedError(f'Нулевая или отрицательная цена запрещена: {sku}')
            model = str(product.model or product.name or sku).strip()
            brand = str(product.brand or settings.KASPI_DEFAULT_BRAND or 'NoBrand').strip()
            if not model or not brand:
                raise XmlFeedError(f'Не заполнены model/brand: {sku}')
            stock = max(0, int(product.stock or 0))
            available = 'yes' if product.status == ProductStatus.ACTIVE and stock != 0 else 'no'
            rows.extend([
                f'    <offer sku="{escape(sku)}">',
                f'      <model>{escape(model)}</model>',
                f'      <brand>{escape(brand)}</brand>',
                '      <availabilities>',
                f'        <availability available="{available}" storeId="{escape(warehouse_id)}" stockCount="{stock}"/>',
                '      </availabilities>',
                f'      <price>{price}</price>',
                '    </offer>',
            ])
            count += 1
        rows.extend(['  </offers>', '</kaspi_catalog>'])
        if count == 0:
            raise XmlFeedError('XML не создан: нет валидных товаров.')
        xml_text = '\n'.join(rows) + '\n'
        try:
            ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise XmlFeedError(f'XML не прошёл проверку синтаксиса: {exc}') from exc
        return xml_text, count

    def _validate(self, *, count: int, expected_count: int, previous_count: int) -> tuple[bool, str]:
        if count <= 0:
            return False, 'Пустой XML запрещён.'
        min_ratio = max(0.1, min(1.0, float(settings.KASPI_XML_MIN_PRODUCT_RATIO or 0.95)))
        minimum = max(1, int(expected_count * min_ratio))
        if expected_count and count < minimum:
            return False, f'Неполный XML: {count} из {expected_count}, минимум {minimum}.'
        max_drop = max(0.0, min(0.9, float(settings.KASPI_XML_MAX_DROP_RATIO or 0.10)))
        if previous_count and count < int(previous_count * (1 - max_drop)):
            return False, f'Опасное уменьшение каталога: было {previous_count}, стало {count}.'
        return True, ''

    def _write_active_file(self, store_id: int, xml_text: str) -> Path:
        path = self.ROOT / f'kaspi_store_{int(store_id)}.xml'
        self.ROOT.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f'kaspi_store_{store_id}_', suffix='.tmp', dir=str(self.ROOT))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(xml_text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return path

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
        queued: int = 0,
        errors: int = 0,
        limit_count: int = 0,
        q_filter: str = '',
        details: list[dict] | None = None,
        source: str = 'manual',
        job_id: int | None = None,
    ) -> dict[str, Any]:
        products_list = list(products)
        xml_text, product_count = self.build_xml(store=store, products=products_list, price_by_sku=price_by_sku, warehouse_id=warehouse_id)
        feed_id = self._feed_id()
        expected_count = len([p for p in products_list if p.kaspi_sku and float(p.current_price or 0) > 0])
        db = SessionLocal()
        try:
            previous = self.active_version(db, int(store.id))
            valid, rejection = self._validate(count=product_count, expected_count=expected_count, previous_count=int(previous.product_count or 0) if previous else 0)
            version = XmlFeedVersion(
                feed_id=feed_id,
                store_id=int(store.id),
                status=XmlFeedStatus.CANDIDATE.value if valid else XmlFeedStatus.REJECTED.value,
                filename=f'kaspi_store_{int(store.id)}.xml',
                merchant_id=str(store.merchant_id or ''),
                warehouse_id=warehouse_id or settings.KASPI_AUTOPILOT_WAREHOUSE_ID or 'PP1',
                xml_text=xml_text,
                details_json=json.dumps(details or [], ensure_ascii=False),
                product_count=product_count,
                expected_count=expected_count,
                changed_count=int(changed or 0),
                unchanged_count=int(skipped or 0),
                skipped_count=int(skipped or 0),
                queued_count=int(queued or 0),
                error_count=int(errors or 0),
                size_bytes=len(xml_text.encode('utf-8')),
                rejection_reason=rejection,
                is_active=False,
                source=str(source or 'manual')[:80],
                job_id=int(job_id) if job_id else None,
            )
            db.add(version)
            db.flush()
            if valid:
                db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store.id), XmlFeedVersion.is_active == True).update(
                    {'is_active': False, 'status': XmlFeedStatus.ARCHIVED.value}, synchronize_session=False
                )
                version.is_active = True
                version.status = XmlFeedStatus.ACTIVE.value
                self._write_active_file(int(store.id), xml_text)
            db.commit()
            return self._record(version, processed=processed, limit_count=limit_count, q_filter=q_filter)
        finally:
            db.close()

    def rebuild_current(self, store_id: int, *, source: str = 'manual', job_id: int | None = None, details: list[dict] | None = None) -> dict[str, Any]:
        db = SessionLocal()
        try:
            store = db.query(Store).filter(Store.id == int(store_id)).first()
            if not store:
                raise XmlFeedError('Магазин не найден.')
            products = self.expected_products(db, int(store_id))
            if not products:
                raise XmlFeedError('Нет активных товаров для полного XML.')
            price_by_sku = {str(p.kaspi_sku): int(round(float(p.current_price or 0))) for p in products}
            store_value = store
            product_values = list(products)
        finally:
            db.close()
        return self.save_feed(
            store=store_value,
            products=product_values,
            price_by_sku=price_by_sku,
            warehouse_id=settings.KASPI_AUTOPILOT_WAREHOUSE_ID or 'PP1',
            changed=sum(1 for x in (details or []) if x.get('changed')),
            skipped=sum(1 for x in (details or []) if not x.get('changed')),
            errors=sum(1 for x in (details or []) if x.get('status') == 'error'),
            details=details or [],
            source=source,
            job_id=job_id,
        )

    def _record(self, version: XmlFeedVersion, **extra: Any) -> dict[str, Any]:
        return {
            'feed_id': version.feed_id,
            'store_id': version.store_id,
            'filename': version.filename,
            'merchant_id': version.merchant_id,
            'warehouse_id': version.warehouse_id,
            'updated_at': version.updated_at.isoformat(timespec='seconds') if version.updated_at else self._now(),
            'size_bytes': version.size_bytes,
            'processed': int(extra.get('processed') or version.product_count),
            'product_count': version.product_count,
            'expected_count': version.expected_count,
            'changed': version.changed_count,
            'skipped': version.skipped_count,
            'queued': version.queued_count,
            'errors': version.error_count,
            'status': version.status,
            'is_active': version.is_active,
            'rejection_reason': version.rejection_reason,
            'source': getattr(version, 'source', '') or 'manual',
            'job_id': getattr(version, 'job_id', None),
            'limit_count': int(extra.get('limit_count') or 0),
            'q_filter': str(extra.get('q_filter') or ''),
            'type': 'XML',
        }

    def get_record(self, store_id: int | None) -> dict[str, Any] | None:
        if not store_id:
            return None
        db = SessionLocal()
        try:
            version = self.active_version(db, int(store_id))
            return self._record(version) if version else None
        finally:
            db.close()

    def list_versions(self, store_id: int | None, limit: int = 20) -> list[dict[str, Any]]:
        if not store_id:
            return []
        db = SessionLocal()
        try:
            rows = db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id)).order_by(XmlFeedVersion.id.desc()).limit(max(1, min(limit, 200))).all()
            return [self._record(row) for row in rows]
        finally:
            db.close()

    def get_version(self, store_id: int | None, feed_id: str | None) -> dict[str, Any] | None:
        if not store_id or not feed_id:
            return None
        db = SessionLocal()
        try:
            row = db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.feed_id == str(feed_id)).first()
            if not row:
                return None
            return {**self._record(row), 'xml_text': row.xml_text, 'details': self._details(row.details_json)}
        finally:
            db.close()

    def compare_versions(self, store_id: int, feed_id: str) -> dict[str, list[dict[str, Any]]]:
        db = SessionLocal()
        try:
            current = db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.feed_id == str(feed_id)).first()
            if not current:
                return {'changed': [], 'added': [], 'removed': []}
            previous = db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.id < current.id).order_by(XmlFeedVersion.id.desc()).first()
            current_rows = {str(x.get('sku')): x for x in self._details(current.details_json) if x.get('sku')}
            previous_rows = {str(x.get('sku')): x for x in self._details(previous.details_json if previous else '[]') if x.get('sku')}
            changed = []
            for sku in sorted(set(current_rows) & set(previous_rows)):
                a, b = previous_rows[sku], current_rows[sku]
                if a.get('new_price') != b.get('new_price'):
                    changed.append({'sku': sku, 'name': b.get('name'), 'old': a.get('new_price'), 'new': b.get('new_price')})
            return {
                'changed': changed,
                'added': [current_rows[x] for x in sorted(set(current_rows) - set(previous_rows))],
                'removed': [previous_rows[x] for x in sorted(set(previous_rows) - set(current_rows))],
            }
        finally:
            db.close()

    def activate_version(self, store_id: int, feed_id: str) -> dict[str, Any]:
        db = SessionLocal()
        try:
            row = db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.feed_id == str(feed_id)).first()
            if not row:
                raise XmlFeedError('Версия XML не найдена.')
            valid, reason = self._validate(count=row.product_count, expected_count=row.expected_count, previous_count=0)
            if not valid:
                raise XmlFeedError(reason)
            db.query(XmlFeedVersion).filter(XmlFeedVersion.store_id == int(store_id), XmlFeedVersion.is_active == True).update(
                {'is_active': False, 'status': XmlFeedStatus.ARCHIVED.value}, synchronize_session=False
            )
            row.is_active = True
            row.status = XmlFeedStatus.ACTIVE.value
            row.rejection_reason = ''
            self._write_active_file(int(store_id), row.xml_text)
            db.add(row)
            db.commit()
            return self._record(row)
        finally:
            db.close()

    def get_xml_text(self, store_id: int | None) -> str | None:
        if not store_id:
            return None
        db = SessionLocal()
        try:
            version = self.active_version(db, int(store_id))
            return version.xml_text if version else None
        finally:
            db.close()

    def log_pull(self, store_id: int | None, request=None, response_status: int = 200) -> dict[str, Any] | None:
        if not store_id:
            return None
        db = SessionLocal()
        try:
            active = self.active_version(db, int(store_id))
            headers = getattr(request, 'headers', {}) or {}
            client = getattr(request, 'client', None)
            forwarded = headers.get('x-forwarded-for', '') if hasattr(headers, 'get') else ''
            ip = (forwarded.split(',')[0].strip() if forwarded else '') or (getattr(client, 'host', '') if client else '')
            user_agent = headers.get('user-agent', '') if hasattr(headers, 'get') else ''
            row = XmlFeedPull(
                store_id=int(store_id),
                feed_id=active.feed_id if active else '',
                path=str(getattr(getattr(request, 'url', None), 'path', '') or ''),
                ip_address=ip,
                user_agent=str(user_agent)[:2000],
                likely_kaspi='kaspi' in str(user_agent).casefold(),
                response_status=int(response_status),
            )
            db.add(row)
            db.commit()
            return {
                'pull_id': row.id,
                'store_id': row.store_id,
                'feed_id': row.feed_id,
                'accessed_at': row.created_at.isoformat(timespec='seconds'),
                'ip': row.ip_address,
                'user_agent': row.user_agent,
                'likely_kaspi': row.likely_kaspi,
                'response_status': row.response_status,
            }
        finally:
            db.close()

    def list_pulls(self, store_id: int | None, limit: int = 50) -> list[dict[str, Any]]:
        if not store_id:
            return []
        db = SessionLocal()
        try:
            rows = db.query(XmlFeedPull).filter(XmlFeedPull.store_id == int(store_id)).order_by(XmlFeedPull.id.desc()).limit(max(1, min(limit, 500))).all()
            return [
                {
                    'pull_id': r.id,
                    'store_id': r.store_id,
                    'feed_id': r.feed_id,
                    'accessed_at': r.created_at.isoformat(timespec='seconds'),
                    'ip': r.ip_address,
                    'user_agent': r.user_agent,
                    'path': r.path,
                    'likely_kaspi': r.likely_kaspi,
                    'response_status': r.response_status,
                }
                for r in rows
            ]
        finally:
            db.close()

    def file_path_for(self, store_id: int | None) -> Path | None:
        if not store_id:
            return None
        path = self.ROOT / f'kaspi_store_{int(store_id)}.xml'
        return path if path.exists() else None


xml_feed_service = XmlFeedService()
