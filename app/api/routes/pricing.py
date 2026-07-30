from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.product import Product
from app.models.store import Store
from app.models.user import User
from app.services.autopilot_service import autopilot_service
from app.services.pricing_engine import pricing_engine

router = APIRouter()


@router.post('/preview/{product_id}')
async def preview_price(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(404, 'Product not found')
    return asdict(await pricing_engine.preview_product(db, product))


@router.post('/apply/{product_id}')
def apply_price(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(404, 'Product not found')
    job = autopilot_service.enqueue(db, product.store_id, mode='single', query_filter=product.kaspi_sku, requested_limit=1)
    return {'status': 'queued', 'job_id': job.id, 'message': 'Цена будет подготовлена в полном XML. Прямой API не используется.'}


@router.post('/run-all')
def run_all(store_id: int = Query(..., ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        raise HTTPException(404, 'Store not found')
    job = autopilot_service.enqueue(db, store.id, mode='api_run_all')
    return {'status': 'queued', 'job_id': job.id, 'store_id': store.id}
