from fastapi import Request
from sqlalchemy.orm import Session
from app.core.security import get_token_from_request, decode_token
from app.models.user import User


def current_user_optional(request: Request, db: Session) -> User | None:
    token = get_token_from_request(request)
    if not token:
        return None
    try:
        payload = decode_token(token)
        return db.query(User).filter(User.id == int(payload['sub'])).first()
    except Exception:
        return None
