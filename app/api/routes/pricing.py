from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.product import Product
from app.models.user import User
from app.services.pricing_engine import pricing_engine

router = APIRouter()


@router.post('/preview/{product_id}')
async def preview_price(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(404, 'Product not found')
    decision = await pricing_engine.preview_product(db, product)
    return decision.__dict__


@router.post('/apply/{product_id}')
async def apply_price(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(404, 'Product not found')
    decision = await pricing_engine.apply_product(db, product)
    return decision.__dict__


@router.post('/run-all')
async def run_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    products = db.query(Product).all()
    result = {'checked': 0, 'changed': 0, 'skipped': 0, 'errors': 0}
    for product in products:
        try:
            decision = await pricing_engine.apply_product(db, product)
            result['checked'] += 1
            if decision.can_apply:
                result['changed'] += 1
            else:
                result['skipped'] += 1
        except Exception:
            result['errors'] += 1
    return result
