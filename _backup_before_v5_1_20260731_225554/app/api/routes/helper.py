from __future__ import annotations

import asyncio
import hmac
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.autopilot import CompetitorSnapshot
from app.models.product import Product, ProductStatus
from app.services.competitor_service import competitor_service
from app.services.helper_session_service import helper_session_service
from app.services.incremental_pricing_service import incremental_pricing_service
from app.services.kaspi_client import KaspiApiError, kaspi_client

router = APIRouter()


class HelperConsentIn(BaseModel):
    consent: bool


class HelperResultIn(BaseModel):
    product_id: int = Field(ge=1)
    public_product_id: str = ''
    lease_token: str = Field(min_length=8, max_length=120)
    status: Literal['ok', 'error']
    payload: Any | None = None
    http_status: int | None = None
    error: str = ''
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86400)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else ''


def _session(db: Session, token: str, request: Request):
    row = helper_session_service.get(db, token)
    row.last_seen_at = datetime.utcnow()
    row.ip_address = _client_ip(request)[:120]
    row.user_agent = request.headers.get('user-agent', '')[:2000]
    db.add(row)
    db.commit()
    return row


@router.get('/{token}/info')
def helper_info(token: str, request: Request, db: Session = Depends(get_db)):
    row = _session(db, token, request)
    ready = db.query(Product).filter(
        Product.store_id == row.store_id,
        Product.status == ProductStatus.ACTIVE,
        Product.auto_pricing_enabled == True,
        Product.min_price > 0,
        Product.max_price > 0,
    ).count()
    return {
        'ok': True,
        'store_id': row.store_id,
        'expires_at': row.expires_at.isoformat(timespec='seconds'),
        'consented': row.consented,
        'status': row.status,
        'ready_count': ready,
        'completed': row.total_completed,
        'success': row.success_count,
        'errors': row.error_count,
        'batch_size': max(1, min(int(settings.HELPER_SESSION_BATCH_SIZE or 25), 50)),
        'delay_seconds': 4,
        'browser_mode': bool(settings.HELPER_BROWSER_ENABLED),
        'offers_base_url': settings.KASPI_PUBLIC_OFFERS_BASE_URL.rstrip('/'),
        'city_id': settings.KASPI_PUBLIC_CITY_ID,
        'offers_limit': int(settings.KASPI_PUBLIC_OFFERS_LIMIT or 10),
        'sort_option': settings.KASPI_PUBLIC_SORT_OPTION,
        'method': settings.KASPI_PUBLIC_OFFERS_METHOD,
    }


@router.post('/{token}/consent')
def helper_consent(token: str, data: HelperConsentIn, request: Request, db: Session = Depends(get_db)):
    row = _session(db, token, request)
    if not data.consent:
        row.status = 'revoked'
        row.revoked_at = datetime.utcnow()
    else:
        row.consented = True
        row.started_at = row.started_at or datetime.utcnow()
    db.add(row)
    db.commit()
    return {'ok': True, 'status': row.status, 'consented': row.consented}


@router.get('/{token}/tasks')
def helper_tasks(
    token: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    session = _session(db, token, request)
    if not session.consented:
        raise HTTPException(status_code=403, detail='Сначала подтвердите согласие.')
    now = datetime.utcnow()
    ttl_minutes = max(1, int(settings.LOCAL_AGENT_CACHE_TTL_MINUTES or 360))
    cutoff = now - timedelta(minutes=ttl_minutes)
    lease_owner = f'helper:{session.id}'
    retry_allowed = or_(CompetitorSnapshot.next_retry_at.is_(None), CompetitorSnapshot.next_retry_at <= now)
    needs_refresh = or_(
        CompetitorSnapshot.id.is_(None),
        and_(
            retry_allowed,
            or_(
                CompetitorSnapshot.fetched_at.is_(None),
                CompetitorSnapshot.fetched_at < cutoff,
                CompetitorSnapshot.status == 'error',
            ),
        ),
    )
    lease_available = or_(
        CompetitorSnapshot.id.is_(None),
        CompetitorSnapshot.lease_until.is_(None),
        CompetitorSnapshot.lease_until <= now,
        CompetitorSnapshot.lease_owner == lease_owner,
    )
    query = (
        db.query(Product, CompetitorSnapshot)
        .outerjoin(CompetitorSnapshot, CompetitorSnapshot.product_id == Product.id)
        .filter(
            Product.store_id == session.store_id,
            Product.status == ProductStatus.ACTIVE,
            Product.auto_pricing_enabled == True,
            Product.min_price > 0,
            Product.max_price > 0,
            needs_refresh,
            lease_available,
        )
        .order_by(func.coalesce(CompetitorSnapshot.fetched_at, datetime(1970, 1, 1)).asc(), Product.id.asc())
    )
    if settings.KASPI_AUTOPILOT_ONLY_IN_STOCK:
        query = query.filter(Product.stock != 0)
    try:
        query = query.with_for_update(of=Product, skip_locked=True)
    except Exception:
        pass
    rows = query.limit(min(limit, max(1, int(settings.HELPER_SESSION_BATCH_SIZE or 25)))).all()
    lease_until = now + timedelta(minutes=max(2, int(settings.LOCAL_AGENT_LEASE_MINUTES or 15)))
    items: list[dict[str, Any]] = []
    for product, snapshot in rows:
        try:
            public_product_id = kaspi_client.extract_public_product_id(product)
        except KaspiApiError:
            continue
        if not snapshot:
            snapshot = CompetitorSnapshot(
                store_id=product.store_id,
                product_id=product.id,
                public_product_id=public_product_id,
                source='browser_helper',
                status='pending',
                offers_json='[]',
            )
        lease_token = uuid.uuid4().hex
        snapshot.lease_owner = lease_owner
        snapshot.lease_token = lease_token
        snapshot.lease_started_at = now
        snapshot.lease_until = lease_until
        db.add(snapshot)
        items.append({
            'product_id': int(product.id),
            'store_id': int(product.store_id),
            'public_product_id': public_product_id,
            'sku': str(product.kaspi_sku or ''),
            'name': str(product.name or ''),
            'url': str(product.url or ''),
            'lease_token': lease_token,
        })
    session.total_assigned = int(session.total_assigned or 0) + len(items)
    session.last_seen_at = now
    db.add(session)
    db.commit()
    return {'ok': True, 'items': items, 'count': len(items)}


@router.post('/{token}/result')
async def helper_result(token: str, data: HelperResultIn, request: Request, db: Session = Depends(get_db)):
    session = _session(db, token, request)
    if not session.consented:
        raise HTTPException(status_code=403, detail='Согласие не подтверждено.')
    product = db.query(Product).filter(Product.id == data.product_id, Product.store_id == session.store_id).first()
    if not product:
        raise HTTPException(status_code=404, detail='Товар не найден.')
    expected_public_id = kaspi_client.extract_public_product_id(product)
    if data.public_product_id and data.public_product_id != expected_public_id:
        raise HTTPException(status_code=409, detail='Некорректный product_id.')
    snapshot = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.product_id == product.id).first()
    lease_owner = f'helper:{session.id}'
    if not snapshot or snapshot.lease_owner != lease_owner or not hmac.compare_digest(str(snapshot.lease_token or ''), data.lease_token):
        raise HTTPException(status_code=409, detail='Задание уже истекло или передано другому помощнику.')

    if data.status == 'ok':
        if data.payload is None:
            raise HTTPException(status_code=422, detail='payload обязателен.')
        snapshot = competitor_service.save_agent_payload(db, product, data.payload, http_status=int(data.http_status or 200))
        snapshot.source = 'browser_helper'
        session.success_count = int(session.success_count or 0) + 1
    else:
        snapshot = competitor_service.record_agent_failure(
            db,
            product,
            error=data.error or f'HTTP {data.http_status or "error"}',
            http_status=data.http_status,
            retry_after_seconds=data.retry_after_seconds,
        )
        snapshot.source = 'browser_helper'
        session.error_count = int(session.error_count or 0) + 1
        session.last_error = str(data.error or '')[:1000]
    snapshot.lease_owner = ''
    snapshot.lease_token = ''
    snapshot.lease_started_at = None
    snapshot.lease_until = None
    session.total_completed = int(session.total_completed or 0) + 1
    session.last_seen_at = datetime.utcnow()
    db.add(snapshot)
    db.add(session)
    db.commit()

    pricing = await incremental_pricing_service.process_product(
        product.id,
        source_device=f'helper-session:{session.id}',
        http_status=data.http_status,
    )
    return {
        'ok': True,
        'product_id': product.id,
        'minimum_price': float(snapshot.minimum_price or 0),
        'pricing': pricing,
        'completed': session.total_completed,
    }


@router.post('/{token}/complete')
async def helper_complete(token: str, request: Request, db: Session = Depends(get_db)):
    session = _session(db, token, request)
    session.status = 'completed'
    session.completed_at = datetime.utcnow()
    db.add(session)
    db.commit()
    store_id = int(session.store_id)
    record = await asyncio.to_thread(incremental_pricing_service.rebuild_xml_now, store_id, finish_job=True)
    return {'ok': True, 'status': session.status, 'xml': record}


@router.post('/{token}/stop')
def helper_stop(token: str, request: Request, db: Session = Depends(get_db)):
    session = _session(db, token, request)
    session.status = 'revoked'
    session.revoked_at = datetime.utcnow()
    db.add(session)
    db.commit()
    return {'ok': True, 'status': session.status}
