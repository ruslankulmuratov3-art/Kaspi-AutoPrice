from fastapi import APIRouter
from app.api.routes import auth, health, stores, products, pricing, admin, reports, kaspi, local_agent

api_router = APIRouter()
api_router.include_router(health.router, tags=['health'])
api_router.include_router(auth.router, prefix='/auth', tags=['auth'])
api_router.include_router(stores.router, prefix='/stores', tags=['stores'])
api_router.include_router(products.router, prefix='/products', tags=['products'])
api_router.include_router(pricing.router, prefix='/pricing', tags=['pricing'])
api_router.include_router(admin.router, prefix='/admin', tags=['admin'])
api_router.include_router(reports.router, prefix='/reports', tags=['reports'])
api_router.include_router(kaspi.router, prefix='/kaspi', tags=['kaspi'])
api_router.include_router(local_agent.router, prefix='/local-agent', tags=['local-agent'])
