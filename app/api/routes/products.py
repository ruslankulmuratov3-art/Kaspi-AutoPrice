from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.product import Product, ProductStatus
from app.models.pricing_rule import PricingRule
from app.models.user import User
from app.repositories.products import products
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate

router = APIRouter()


@router.get('', response_model=list[ProductRead])
def list_products(q: str = Query('', max_length=120), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if q:
        return products.search(db, q)
    return products.list(db, limit=200)


@router.post('', response_model=ProductRead)
def create_product(payload: ProductCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = Product(**payload.model_dump())
    db.add(product)
    db.flush()
    db.add(PricingRule(product_id=product.id))
    db.commit()
    db.refresh(product)
    return product


@router.get('/{product_id}', response_model=ProductRead)
def read_product(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = products.get(db, product_id)
    if not product:
        raise HTTPException(404, 'Product not found')
    return product


@router.patch('/{product_id}', response_model=ProductRead)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = products.get(db, product_id)
    if not product:
        raise HTTPException(404, 'Product not found')
    return products.update(db, product, **payload.model_dump(exclude_unset=True))


@router.delete('/{product_id}', status_code=204)
def archive_product(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = products.get(db, product_id)
    if not product:
        raise HTTPException(404, 'Product not found')
    product.status = ProductStatus.ARCHIVED
    product.auto_pricing_enabled = False
    db.add(product)
    db.commit()
    return Response(status_code=204)
