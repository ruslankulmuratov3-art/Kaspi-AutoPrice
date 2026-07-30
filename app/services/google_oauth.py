from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from jose import jwt

from app.core.config import settings

AUTHORIZATION_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
JWKS_ENDPOINT = 'https://www.googleapis.com/oauth2/v3/certs'
_VALID_ISSUERS = {'accounts.google.com', 'https://accounts.google.com'}


def google_enabled() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID.strip() and settings.GOOGLE_CLIENT_SECRET.strip())


class GoogleOAuthService:
    def authorization_url(self, request: Request, redirect_uri: str) -> str:
        if not google_enabled():
            raise HTTPException(status_code=503, detail='Вход через Google не настроен')
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        request.session['google_oauth_state'] = state
        request.session['google_oauth_nonce'] = nonce
        params = {
            'client_id': settings.GOOGLE_CLIENT_ID.strip(),
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'nonce': nonce,
            'prompt': 'select_account',
        }
        return AUTHORIZATION_ENDPOINT + '?' + urlencode(params)

    async def exchange_code(self, request: Request, *, code: str, state: str, redirect_uri: str) -> dict:
        expected_state = str(request.session.pop('google_oauth_state', '') or '')
        expected_nonce = str(request.session.pop('google_oauth_nonce', '') or '')
        if not expected_state or not hmac.compare_digest(expected_state, state or ''):
            raise HTTPException(status_code=400, detail='Google OAuth state не совпал')
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            token_response = await client.post(TOKEN_ENDPOINT, data={
                'code': code,
                'client_id': settings.GOOGLE_CLIENT_ID.strip(),
                'client_secret': settings.GOOGLE_CLIENT_SECRET.strip(),
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            })
            if token_response.status_code >= 400:
                raise HTTPException(status_code=400, detail='Google не выдал токен входа')
            token_data = token_response.json()
            id_token = str(token_data.get('id_token') or '')
            if not id_token:
                raise HTTPException(status_code=400, detail='Google не вернул ID token')
            jwks_response = await client.get(JWKS_ENDPOINT)
            jwks_response.raise_for_status()
            jwks = jwks_response.json().get('keys') or []

        header = jwt.get_unverified_header(id_token)
        kid = header.get('kid')
        key = next((item for item in jwks if item.get('kid') == kid), None)
        if not key:
            raise HTTPException(status_code=400, detail='Не найден ключ подписи Google')
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[header.get('alg', 'RS256')],
                audience=settings.GOOGLE_CLIENT_ID.strip(),
                options={'verify_at_hash': False},
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail='Подпись Google ID token не прошла проверку') from exc
        if claims.get('iss') not in _VALID_ISSUERS:
            raise HTTPException(status_code=400, detail='Некорректный issuer Google')
        if expected_nonce and not hmac.compare_digest(str(claims.get('nonce') or ''), expected_nonce):
            raise HTTPException(status_code=400, detail='Google nonce не совпал')
        if not claims.get('email'):
            raise HTTPException(status_code=400, detail='Google не вернул email')
        return dict(claims)


google_oauth = GoogleOAuthService()
