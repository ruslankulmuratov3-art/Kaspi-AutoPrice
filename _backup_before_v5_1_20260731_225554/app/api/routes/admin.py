from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.core.database import get_db
from app.models.user import User
from app.models.audit import AuditLog
from app.models.task_log import TaskLog
from app.models.alert import Alert

router = APIRouter()


@router.get('/users')
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return db.query(User).order_by(User.id.asc()).all()


@router.get('/audit')
def audit_logs(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return db.query(AuditLog).order_by(AuditLog.id.desc()).limit(200).all()


@router.get('/tasks')
def task_logs(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return db.query(TaskLog).order_by(TaskLog.id.desc()).limit(200).all()


@router.get('/alerts')
def alerts(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return db.query(Alert).order_by(Alert.id.desc()).limit(200).all()
