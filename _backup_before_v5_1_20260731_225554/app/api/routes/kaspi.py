from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.store import Store
from app.models.user import User
from app.services.kaspi_client import kaspi_client, KaspiApiError

router = APIRouter()


@router.get('/test')
async def test_kaspi(store_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first() if store_id else None
    try:
        return await kaspi_client.test_connection(store)
    except KaspiApiError as exc:
        raise HTTPException(400, str(exc))


@router.get('/import-schema')
async def import_schema(store_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first() if store_id else None
    try:
        return await kaspi_client.get_import_schema(store)
    except KaspiApiError as exc:
        raise HTTPException(400, str(exc))


@router.get('/import-status/{code}')
async def import_status(code: str, store_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first() if store_id else None
    try:
        result = await kaspi_client.get_import_status(code, store)
        return result.__dict__
    except KaspiApiError as exc:
        raise HTTPException(400, str(exc))


@router.get('/import-result/{code}')
async def import_result(code: str, store_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    store = db.query(Store).filter(Store.id == store_id).first() if store_id else None
    try:
        return await kaspi_client.get_import_result(code, store)
    except KaspiApiError as exc:
        raise HTTPException(400, str(exc))
