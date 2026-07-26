import json
from sqlalchemy.orm import Session
from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    def __init__(self):
        super().__init__(AuditLog)

    def write(self, db: Session, action: str, entity: str = '', entity_id: str = '', user_id: int | None = None, ip_address: str = '', meta: dict | None = None) -> AuditLog:
        log = AuditLog(action=action, entity=entity, entity_id=str(entity_id), user_id=user_id, ip_address=ip_address, meta_json=json.dumps(meta or {}, ensure_ascii=False))
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

audit = AuditRepository()
