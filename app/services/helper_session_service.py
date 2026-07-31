from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.helper import HelperSession


@dataclass(slots=True)
class CreatedHelperSession:
    row: HelperSession
    token: str


class HelperSessionService:
    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()

    def create(self, db: Session, *, store_id: int, user_id: int | None = None) -> CreatedHelperSession:
        now = datetime.utcnow()
        db.query(HelperSession).filter(
            HelperSession.store_id == int(store_id),
            HelperSession.status == 'active',
            HelperSession.expires_at <= now,
        ).update({'status': 'expired'}, synchronize_session=False)
        token = secrets.token_urlsafe(32)
        row = HelperSession(
            token_hash=self.token_hash(token),
            store_id=int(store_id),
            created_by_user_id=int(user_id) if user_id else None,
            status='active',
            expires_at=now + timedelta(minutes=max(10, int(settings.HELPER_SESSION_EXPIRE_MINUTES or 180))),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return CreatedHelperSession(row=row, token=token)

    def get(self, db: Session, token: str, *, require_active: bool = True) -> HelperSession:
        row = db.query(HelperSession).filter(HelperSession.token_hash == self.token_hash(token)).first()
        if not row:
            raise HTTPException(status_code=404, detail='Ссылка помощника недействительна.')
        now = datetime.utcnow()
        if row.expires_at <= now and row.status == 'active':
            row.status = 'expired'
            db.add(row)
            db.commit()
        if require_active and row.status != 'active':
            raise HTTPException(status_code=410, detail='Ссылка помощника истекла или была отключена.')
        return row

    def revoke(self, db: Session, token: str) -> HelperSession:
        row = self.get(db, token, require_active=False)
        row.status = 'revoked'
        row.revoked_at = datetime.utcnow()
        db.add(row)
        db.commit()
        return row


helper_session_service = HelperSessionService()
