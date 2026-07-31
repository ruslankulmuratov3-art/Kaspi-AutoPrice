from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

try:  # Production keeps using python-jose/passlib when installed.
    from jose import JWTError, jwt  # type: ignore
except Exception:  # pragma: no cover - lightweight local/test fallback
    JWTError = Exception
    jwt = None

try:
    from passlib.context import CryptContext  # type: ignore
except Exception:  # pragma: no cover - lightweight local/test fallback
    CryptContext = None

ALGORITHM = 'HS256'
TOKEN_COOKIE_NAME = 'kaspi_access_token'
_pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto') if CryptContext else None
_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt='kaspi-access-token-v1')


def _pbkdf2_hash(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    rounds = 260_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
    return 'pbkdf2_sha256${}${}${}'.format(
        rounds,
        base64.urlsafe_b64encode(salt).decode().rstrip('='),
        base64.urlsafe_b64encode(digest).decode().rstrip('='),
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if _pwd_context and not str(hashed_password or '').startswith('pbkdf2_sha256$'):
        try:
            return bool(_pwd_context.verify(plain_password, hashed_password))
        except Exception:
            return False
    try:
        scheme, rounds, salt, digest = str(hashed_password or '').split('$', 3)
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), _b64decode(salt), int(rounds))
        return hmac.compare_digest(actual, _b64decode(digest))
    except Exception:
        return False


def hash_password(password: str) -> str:
    if _pwd_context:
        return str(_pwd_context.hash(password))
    return _pbkdf2_hash(password)


def create_access_token(subject: str, extra: Optional[Dict[str, Any]] = None, expires_delta: Optional[timedelta] = None) -> str:
    expires = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires
    payload: Dict[str, Any] = {
        'sub': str(subject),
        'exp': int(expire.timestamp()),
        'iat': int(datetime.now(timezone.utc).timestamp()),
    }
    if extra:
        payload.update(extra)
    if jwt is not None:
        return str(jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM))
    return _serializer.dumps(payload)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        if jwt is not None:
            return dict(jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM]))
        payload = dict(_serializer.loads(token, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60))
        if int(payload.get('exp') or 0) < int(datetime.now(timezone.utc).timestamp()):
            raise SignatureExpired('expired')
        return payload
    except (JWTError, BadSignature, SignatureExpired, ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid authentication token') from exc


def get_token_from_request(request: Request) -> Optional[str]:
    cookie = request.cookies.get(TOKEN_COOKIE_NAME)
    if cookie:
        return cookie
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth.split(' ', 1)[1].strip()
    return None


def require_token(request: Request) -> Dict[str, Any]:
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Not authenticated')
    return decode_token(token)
