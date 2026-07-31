from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.report_service import report_service

router = APIRouter()


@router.get('/dashboard')
def dashboard_metrics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return report_service.dashboard_metrics(db)


@router.get('/products.csv')
def export_products(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    csv_text = report_service.export_products_csv(db)
    return Response(content=csv_text, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename=products.csv'})
