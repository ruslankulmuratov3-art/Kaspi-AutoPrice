from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.competitor_service import competitor_service

router = APIRouter()


@router.get('/health')
def health(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {
        'status': 'ok',
        'app': settings.APP_NAME,
        'version': settings.APP_VERSION,
        'environment': settings.ENVIRONMENT,
        'database': 'ok',
        'direct_price_api': bool(settings.KASPI_DIRECT_PRICE_API_ENABLED and settings.KASPI_DIRECT_PRICE_UPDATE_PATH),
        'competitor_source': competitor_service.state_info(db),
    }
