from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.repositories.users import users
from app.core.security import verify_password, create_access_token
from app.models.user import User


class AuthService:
    def authenticate(self, db: Session, username: str, password: str) -> User:
        user = users.by_username(db, username) or users.by_email(db, username)
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Неверный логин или пароль')
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Пользователь отключён')
        return user

    def token_for_user(self, user: User) -> str:
        return create_access_token(str(user.id), extra={'role': user.role.value, 'username': user.username})

auth_service = AuthService()
