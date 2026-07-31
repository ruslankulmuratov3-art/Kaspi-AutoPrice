from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import TOKEN_COOKIE_NAME
from app.schemas.user import LoginPayload
from app.services.auth_service import auth_service

router = APIRouter()


@router.post('/login')
def login(payload: LoginPayload, response: Response, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, payload.username, payload.password)
    token = auth_service.token_for_user(user)
    response.set_cookie(TOKEN_COOKIE_NAME, token, httponly=True, samesite='lax')
    return {'access_token': token, 'token_type': 'bearer', 'user': {'id': user.id, 'username': user.username, 'role': user.role.value}}


@router.post('/logout')
def logout(response: Response):
    response.delete_cookie(TOKEN_COOKIE_NAME)
    return {'message': 'ok'}
