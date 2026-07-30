from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.access import AgentDevice
from app.models.autopilot import CompetitorSnapshot
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.services.access_service import access_service
from app.services.autopilot_service import autopilot_service
from app.services.competitor_service import competitor_service
from app.services.kaspi_client import KaspiApiError, kaspi_client

router = APIRouter()
_AGENT_ID_RE = re.compile(r'[^a-zA-Z0-9_.-]+')


@dataclass(slots=True)
class AgentPrincipal:
    agent_id: str
    lease_owner: str
    device_id: int | None = None
    user_id: int | None = None
    legacy: bool = False


class AgentPairIn(BaseModel):
    code: str = Field(..., min_length=8, max_length=80)
    device_name: str = Field(..., min_length=2, max_length=120)
    platform: str = Field(default='unknown', max_length=80)


class AgentResultIn(BaseModel):
    product_id: int = Field(..., ge=1, description='Internal database product id from /tasks')
    public_product_id: str = ''
    lease_token: str = Field(..., min_length=8, max_length=120)
    status: Literal['ok', 'error']
    payload: Any | None = None
    http_status: int | None = None
    error: str = ''
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86400)


class AgentRunIn(BaseModel):
    store_id: int = Field(..., ge=1)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '').split(',', 1)[0].strip()
    if forwarded:
        return forwarded[:80]
    return (request.client.host if request.client else '')[:80]


def require_agent(
    request: Request,
    x_agent_token: str = Header(default='', alias='X-Agent-Token'),
    x_agent_id: str = Header(default='default-device', alias='X-Agent-ID'),
    db: Session = Depends(get_db),
) -> AgentPrincipal:
    """Authenticate an explicitly paired device or the optional legacy shared token."""
    if not settings.LOCAL_AGENT_ENABLED:
        raise HTTPException(status_code=503, detail='Local agent is disabled')

    token = x_agent_token.strip()
    device = access_service.authenticate_device(db, token)
    if device:
        device.last_seen_at = datetime.utcnow()
        device.last_ip = _client_ip(request)
        device.last_user_agent = request.headers.get('user-agent', '')[:1000]
        db.add(device)
        db.commit()
        safe_name = _AGENT_ID_RE.sub('-', device.name.strip())[:60].strip('-') or f'device-{device.id}'
        return AgentPrincipal(
            agent_id=f'{safe_name}-{device.id}',
            lease_owner=f'device:{device.id}',
            device_id=device.id,
            user_id=device.user_id,
        )

    expected = settings.LOCAL_AGENT_TOKEN.strip()
    if settings.LOCAL_AGENT_ALLOW_LEGACY_TOKEN and expected and hmac.compare_digest(token, expected):
        agent_id = _AGENT_ID_RE.sub('-', (x_agent_id or '').strip())[:80].strip('-')
        if not agent_id:
            raise HTTPException(status_code=422, detail='X-Agent-ID is required')
        return AgentPrincipal(agent_id=agent_id, lease_owner=f'legacy:{agent_id}', legacy=True)

    raise HTTPException(status_code=401, detail='Device token is invalid or revoked')


def _principal(value: AgentPrincipal | str) -> AgentPrincipal:
    if isinstance(value, AgentPrincipal):
        return value
    safe = _AGENT_ID_RE.sub('-', str(value or '').strip())[:80].strip('-') or 'test-device'
    return AgentPrincipal(agent_id=safe, lease_owner=safe, legacy=True)


@router.post('/pair')
def pair_device(data: AgentPairIn, request: Request, db: Session = Depends(get_db)):
    if not settings.LOCAL_AGENT_ENABLED:
        raise HTTPException(status_code=503, detail='Local agent is disabled')
    result = access_service.pair_device(
        db,
        code=data.code,
        name=data.device_name,
        platform=data.platform,
    )
    result.device.last_seen_at = datetime.utcnow()
    result.device.last_ip = _client_ip(request)
    result.device.last_user_agent = request.headers.get('user-agent', '')[:1000]
    db.add(result.device)
    db.commit()
    return {
        'ok': True,
        'token': result.token,
        'device_id': result.device.id,
        'device_key': result.device.device_key,
        'device_name': result.device.name,
        'message': 'Устройство зарегистрировано. Токен показывается только один раз.',
    }


@router.get('/tasks')
def agent_tasks(
    store_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    agent_id: AgentPrincipal | str = Depends(require_agent),
    db: Session = Depends(get_db),
):
    principal = _principal(agent_id)
    now = datetime.utcnow()
    ttl_minutes = max(1, int(settings.LOCAL_AGENT_CACHE_TTL_MINUTES or 360))
    lease_minutes = max(2, min(int(settings.LOCAL_AGENT_LEASE_MINUTES or 15), 120))
    cutoff = now - timedelta(minutes=ttl_minutes)
    limit = min(limit, max(1, int(settings.LOCAL_AGENT_BATCH_SIZE or limit)))

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
        CompetitorSnapshot.lease_owner == principal.lease_owner,
    )

    query = (
        db.query(Product, CompetitorSnapshot)
        .outerjoin(CompetitorSnapshot, CompetitorSnapshot.product_id == Product.id)
        .filter(
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
    if store_id:
        query = query.filter(Product.store_id == store_id)
    try:
        query = query.with_for_update(of=Product, skip_locked=True)
    except Exception:
        pass

    rows = query.limit(limit).all()
    items: list[dict[str, Any]] = []
    lease_until = now + timedelta(minutes=lease_minutes)
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
                source=competitor_service.AGENT_SOURCE,
                status='pending',
                offers_json='[]',
            )
        lease_token = uuid.uuid4().hex
        snapshot.lease_owner = principal.lease_owner
        snapshot.lease_token = lease_token
        snapshot.lease_started_at = now
        snapshot.lease_until = lease_until
        db.add(snapshot)
        items.append({
            'product_id': int(product.id),
            'public_product_id': public_product_id,
            'store_id': int(product.store_id),
            'sku': str(product.kaspi_sku or ''),
            'name': str(product.name or ''),
            'url': str(product.url or ''),
            'lease_token': lease_token,
            'lease_until': lease_until.isoformat(timespec='seconds'),
            'agent_id': principal.agent_id,
            'last_success_at': snapshot.fetched_at.isoformat(timespec='seconds') if snapshot.fetched_at else None,
        })
    if principal.device_id:
        device = db.query(AgentDevice).filter(AgentDevice.id == principal.device_id).first()
        if device:
            device.tasks_requested = int(device.tasks_requested or 0) + len(items)
            device.last_seen_at = now
            db.add(device)
    db.commit()
    return {'items': items, 'count': len(items), 'agent_id': principal.agent_id, 'server_time': now.isoformat(timespec='seconds')}


@router.post('/result')
def agent_result(
    data: AgentResultIn,
    agent_id: AgentPrincipal | str = Depends(require_agent),
    db: Session = Depends(get_db),
):
    principal = _principal(agent_id)
    product = db.query(Product).filter(Product.id == data.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail='Product not found')
    expected_public_id = kaspi_client.extract_public_product_id(product)
    if data.public_product_id and data.public_product_id != expected_public_id:
        raise HTTPException(status_code=409, detail='Public product id mismatch')

    snapshot = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.product_id == product.id).first()
    if not snapshot or snapshot.lease_owner != principal.lease_owner or not hmac.compare_digest(str(snapshot.lease_token or ''), data.lease_token):
        raise HTTPException(status_code=409, detail='Task lease expired or belongs to another device')

    if data.status == 'ok':
        if data.payload is None:
            raise HTTPException(status_code=422, detail='payload is required for ok status')
        snapshot = competitor_service.save_agent_payload(
            db,
            product,
            data.payload,
            http_status=int(data.http_status or 200),
        )
        offers_count = len(competitor_service._offers_from_snapshot(snapshot))
    else:
        snapshot = competitor_service.record_agent_failure(
            db,
            product,
            error=data.error or f'HTTP {data.http_status or "error"}',
            http_status=data.http_status,
            retry_after_seconds=data.retry_after_seconds,
        )
        offers_count = 0

    snapshot.lease_owner = ''
    snapshot.lease_token = ''
    snapshot.lease_started_at = None
    snapshot.lease_until = None
    db.add(snapshot)
    if principal.device_id:
        device = db.query(AgentDevice).filter(AgentDevice.id == principal.device_id).first()
        if device:
            if data.status == 'ok':
                device.results_ok = int(device.results_ok or 0) + 1
            else:
                device.results_error = int(device.results_error or 0) + 1
            device.last_seen_at = datetime.utcnow()
            db.add(device)
    db.commit()

    if data.status == 'ok':
        return {
            'ok': True,
            'product_id': product.id,
            'offers': offers_count,
            'minimum_price': snapshot.minimum_price,
            'cache_until': snapshot.expires_at.isoformat(timespec='seconds') if snapshot.expires_at else None,
        }
    return {
        'ok': True,
        'product_id': product.id,
        'retry_at': snapshot.next_retry_at.isoformat(timespec='seconds') if snapshot.next_retry_at else None,
    }


@router.post('/run-autopilot')
def agent_run_autopilot(
    data: AgentRunIn,
    agent_id: AgentPrincipal | str = Depends(require_agent),
    db: Session = Depends(get_db),
):
    principal = _principal(agent_id)
    store = db.query(Store).filter(Store.id == data.store_id, Store.is_active == True).first()
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')
    job = autopilot_service.enqueue(db, store.id, mode='local_agent_sync')
    return {'ok': True, 'job_id': job.id, 'status': job.status.value, 'agent_id': principal.agent_id}


@router.get('/status')
def agent_status(agent_id: AgentPrincipal | str = Depends(require_agent), db: Session = Depends(get_db)):
    principal = _principal(agent_id)
    now = datetime.utcnow()
    successful = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.source == competitor_service.AGENT_SOURCE, CompetitorSnapshot.fetched_at.isnot(None)).count()
    errors = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.source == competitor_service.AGENT_SOURCE, CompetitorSnapshot.status == 'error').count()
    active_leases = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.lease_until > now).count()
    return {
        'enabled': True,
        'agent_id': principal.agent_id,
        'device_id': principal.device_id,
        'successful_products': successful,
        'error_products': errors,
        'active_leases': active_leases,
        'server_time': now.isoformat(timespec='seconds'),
    }
