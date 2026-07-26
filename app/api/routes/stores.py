from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.repositories.stores import stores
from app.schemas.store import StoreCreate, StoreRead, StoreUpdate

router = APIRouter()


@router.get('', response_model=list[StoreRead])
def list_stores(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return stores.list(db)


@router.post('', response_model=StoreRead)
def create_store(payload: StoreCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return stores.create(db, owner_id=user.id, **payload.model_dump())


@router.patch('/{store_id}', response_model=StoreRead)
def update_store(store_id: int, payload: StoreUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = stores.get(db, store_id)
    if not store:
        raise HTTPException(404, 'Store not found')
    return stores.update(db, store, **payload.model_dump(exclude_unset=True))


@router.delete('/{store_id}')
def delete_store(store_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = stores.get(db, store_id)
    if not store:
        raise HTTPException(404, 'Store not found')
    stores.delete(db, store)
    return {'message': 'deleted'}
