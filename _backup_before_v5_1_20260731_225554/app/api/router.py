from fastapi import APIRouter

from app.api.routes import auth, health, stores, products, pricing, reports, kaspi, helper
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(health.router, tags=['health'])
api_router.include_router(auth.router, prefix='/auth', tags=['auth'])
api_router.include_router(stores.router, prefix='/stores', tags=['stores'])
api_router.include_router(products.router, prefix='/products', tags=['products'])
api_router.include_router(pricing.router, prefix='/pricing', tags=['pricing'])
api_router.include_router(reports.router, prefix='/reports', tags=['reports'])
api_router.include_router(kaspi.router, prefix='/kaspi', tags=['kaspi'])
api_router.include_router(helper.router, prefix='/helper', tags=['helper'])

# Old registration/device APIs are kept in source only for safe rollback and
# database compatibility. They are not exposed in the simplified production UI.
if settings.LEGACY_ACCESS_UI_ENABLED:
    from app.api.routes import admin, local_agent

    api_router.include_router(admin.router, prefix='/admin', tags=['admin'])
    api_router.include_router(local_agent.router, prefix='/local-agent', tags=['local-agent'])
