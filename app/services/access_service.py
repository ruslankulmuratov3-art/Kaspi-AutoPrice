from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models.access import AgentDevice, InviteCode, InviteKind
from app.models.user import User, UserRole

_CODE_CLEAN_RE = re.compile(r'[^A-Z0-9]+')
_USERNAME_RE = re.compile(r'[^a-zA-Z0-9_.-]+')


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_code(value: str) -> str:
    return _CODE_CLEAN_RE.sub('', (value or '').upper())


def hash_secret(value: str) -> str:
    return hashlib.sha256((value or '').encode('utf-8')).hexdigest()


def _group(raw: str, size: int = 4) -> str:
    return '-'.join(raw[i:i + size] for i in range(0, len(raw), size))


def generate_human_code(prefix: str) -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    raw = ''.join(secrets.choice(alphabet) for _ in range(12))
    return f'{prefix}-{_group(raw)}'


def generate_device_token() -> str:
    return 'kat_' + secrets.token_urlsafe(40)


def normalize_email(value: str) -> str:
    try:
        return validate_email((value or '').strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise HTTPException(status_code=422, detail='Некорректный email') from exc


def normalize_username(value: str, fallback_email: str = '') -> str:
    base = (value or '').strip()
    if not base and fallback_email:
        base = fallback_email.split('@', 1)[0]
    base = _USERNAME_RE.sub('-', base).strip('-.').lower()[:60]
    if len(base) < 3:
        base = f'user-{secrets.token_hex(3)}'
    return base


@dataclass(slots=True)
class PairResult:
    device: AgentDevice
    token: str


class AccessService:
    def _unique_username(self, db: Session, requested: str, email: str) -> str:
        base = normalize_username(requested, email)
        candidate = base
        suffix = 1
        while db.query(User.id).filter(User.username == candidate).first():
            suffix += 1
            candidate = f'{base[:52]}-{suffix}'
        return candidate

    def create_invite(
        self,
        db: Session,
        *,
        kind: InviteKind,
        created_by_id: int,
        assigned_user_id: int | None = None,
        expires_hours: int | None = None,
        max_uses: int = 1,
        note: str = '',
    ) -> tuple[InviteCode, str]:
        if kind == InviteKind.ACCOUNT:
            prefix = 'USR'
            default_hours = settings.ACCOUNT_INVITE_EXPIRE_HOURS
            assigned_user_id = None
        else:
            prefix = 'DEV'
            default_hours = settings.DEVICE_INVITE_EXPIRE_HOURS
            if not assigned_user_id:
                raise HTTPException(status_code=422, detail='Для кода устройства выбери пользователя')
        plain = generate_human_code(prefix)
        normalized = normalize_code(plain)
        hours = max(1, min(int(expires_hours or default_hours), 24 * 30))
        invite = InviteCode(
            kind=kind,
            code_hash=hash_secret(normalized),
            code_prefix=plain[:8] + '…',
            created_by_id=created_by_id,
            assigned_user_id=assigned_user_id,
            note=(note or '').strip()[:255],
            max_uses=max(1, min(int(max_uses or 1), 100)),
            used_count=0,
            expires_at=utcnow() + timedelta(hours=hours),
            is_active=True,
        )
        db.add(invite)
        db.commit()
        db.refresh(invite)
        return invite, plain

    def _get_valid_invite(
        self,
        db: Session,
        plain_code: str,
        kind: InviteKind,
        *,
        assigned_user_id: int | None = None,
    ) -> InviteCode:
        normalized = normalize_code(plain_code)
        if len(normalized) < 10:
            raise HTTPException(status_code=422, detail='Код доступа введён неверно')
        query = db.query(InviteCode).filter(
            InviteCode.code_hash == hash_secret(normalized),
            InviteCode.kind == kind,
        )
        try:
            query = query.with_for_update()
        except Exception:
            pass
        invite = query.first()
        now = utcnow()
        if not invite or not invite.is_active:
            raise HTTPException(status_code=403, detail='Код доступа недействителен')
        if invite.expires_at and invite.expires_at <= now:
            invite.is_active = False
            db.add(invite)
            db.commit()
            raise HTTPException(status_code=403, detail='Срок действия кода истёк')
        if invite.used_count >= invite.max_uses:
            invite.is_active = False
            db.add(invite)
            db.commit()
            raise HTTPException(status_code=403, detail='Код уже использован')
        if assigned_user_id and invite.assigned_user_id and int(invite.assigned_user_id) != int(assigned_user_id):
            raise HTTPException(status_code=403, detail='Код выдан другому пользователю')
        return invite

    def consume_invite(self, db: Session, invite: InviteCode) -> None:
        invite.used_count = int(invite.used_count or 0) + 1
        invite.last_used_at = utcnow()
        if invite.used_count >= invite.max_uses:
            invite.is_active = False
        db.add(invite)

    def register_password_user(
        self,
        db: Session,
        *,
        email: str,
        username: str,
        password: str,
        invite_code: str,
    ) -> User:
        if not settings.REGISTRATION_ENABLED:
            raise HTTPException(status_code=403, detail='Регистрация отключена')
        email = normalize_email(email)
        if db.query(User.id).filter(User.email == email).first():
            raise HTTPException(status_code=409, detail='Пользователь с таким email уже существует')
        if len(password or '') < 8:
            raise HTTPException(status_code=422, detail='Пароль должен содержать минимум 8 символов')
        if len(password) > 72:
            raise HTTPException(status_code=422, detail='Пароль слишком длинный')
        invite = self._get_valid_invite(db, invite_code, InviteKind.ACCOUNT)
        role = UserRole.VIEWER
        try:
            role = UserRole(settings.REGISTRATION_DEFAULT_ROLE)
        except ValueError:
            pass
        user = User(
            email=email,
            username=self._unique_username(db, username, email),
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            auth_provider='password',
            email_verified=False,
            last_login_at=utcnow(),
        )
        db.add(user)
        self.consume_invite(db, invite)
        db.commit()
        db.refresh(user)
        return user

    def login_or_register_google(self, db: Session, profile: dict, invite_code: str = '') -> User:
        email = normalize_email(str(profile.get('email') or ''))
        if not bool(profile.get('email_verified')):
            raise HTTPException(status_code=403, detail='Google email не подтверждён')
        google_sub = str(profile.get('sub') or '').strip()
        if not google_sub:
            raise HTTPException(status_code=400, detail='Google не вернул идентификатор пользователя')
        user = db.query(User).filter((User.google_sub == google_sub) | (User.email == email)).first()
        if user:
            if not user.is_active:
                raise HTTPException(status_code=403, detail='Пользователь отключён администратором')
            if not user.google_sub:
                user.google_sub = google_sub
            user.auth_provider = 'google'
            user.email_verified = bool(profile.get('email_verified', True))
            user.full_name = str(profile.get('name') or user.full_name or '')[:160]
            user.avatar_url = str(profile.get('picture') or user.avatar_url or '')[:500]
            user.last_login_at = utcnow()
            db.add(user)
            db.commit()
            db.refresh(user)
            return user
        if not settings.REGISTRATION_ENABLED:
            raise HTTPException(status_code=403, detail='Регистрация отключена')
        invite = self._get_valid_invite(db, invite_code, InviteKind.ACCOUNT)
        role = UserRole.VIEWER
        try:
            role = UserRole(settings.REGISTRATION_DEFAULT_ROLE)
        except ValueError:
            pass
        user = User(
            email=email,
            username=self._unique_username(db, '', email),
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=role,
            is_active=True,
            full_name=str(profile.get('name') or '')[:160],
            avatar_url=str(profile.get('picture') or '')[:500],
            auth_provider='google',
            google_sub=google_sub,
            email_verified=bool(profile.get('email_verified', True)),
            last_login_at=utcnow(),
        )
        db.add(user)
        self.consume_invite(db, invite)
        db.commit()
        db.refresh(user)
        return user

    def pair_device(self, db: Session, *, code: str, name: str, platform: str = 'unknown') -> PairResult:
        invite = self._get_valid_invite(db, code, InviteKind.DEVICE)
        if not invite.assigned_user_id:
            raise HTTPException(status_code=422, detail='Код устройства не привязан к пользователю')
        active_count = db.query(AgentDevice.id).filter(
            AgentDevice.user_id == invite.assigned_user_id,
            AgentDevice.is_active == True,
        ).count()
        if active_count >= max(1, int(settings.LOCAL_AGENT_MAX_TRUSTED_DEVICES or 4)):
            raise HTTPException(status_code=409, detail='Достигнут лимит активных устройств для пользователя')
        token = generate_device_token()
        device = AgentDevice(
            user_id=invite.assigned_user_id,
            name=(name or 'Новое устройство').strip()[:120],
            device_key=secrets.token_hex(24),
            token_hash=hash_secret(token),
            token_prefix=token[:12] + '…',
            platform=(platform or 'unknown').strip()[:80],
            is_active=True,
        )
        db.add(device)
        self.consume_invite(db, invite)
        db.commit()
        db.refresh(device)
        return PairResult(device=device, token=token)

    def authenticate_device(self, db: Session, token: str) -> AgentDevice | None:
        token = (token or '').strip()
        if not token:
            return None
        token_hash = hash_secret(token)
        device = (
            db.query(AgentDevice)
            .join(User, User.id == AgentDevice.user_id)
            .filter(
                AgentDevice.token_hash == token_hash,
                AgentDevice.is_active == True,
                User.is_active == True,
            )
            .first()
        )
        return device

    def revoke_device(self, db: Session, device: AgentDevice) -> None:
        device.is_active = False
        device.revoked_at = utcnow()
        db.add(device)
        db.commit()


access_service = AccessService()
