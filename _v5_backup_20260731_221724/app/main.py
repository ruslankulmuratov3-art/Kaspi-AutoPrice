from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.web.router import web_router
from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.core.logging import configure_logging, get_logger
from app.services.autopilot_service import autopilot_service

configure_logging()
logger = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url='/api/docs' if settings.ENABLE_DOCS else None,
        redoc_url='/api/redoc' if settings.ENABLE_DOCS else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

    app.mount('/static', StaticFiles(directory='app/static'), name='static')
    app.include_router(web_router)
    app.include_router(api_router, prefix='/api')

    @app.middleware('http')
    async def viewer_access_guard(request: Request, call_next):
        path = request.url.path
        public_prefixes = (
            '/static', '/login', '/register', '/auth/google', '/logout',
            '/api/auth', '/api/local-agent', '/kaspi-feed', '/health',
        )
        if path == '/' or not path.startswith(public_prefixes):
            from app.models.user import UserRole
            from app.web.deps import current_user_optional
            db = SessionLocal()
            try:
                user = current_user_optional(request, db)
                if user and user.role == UserRole.VIEWER:
                    if path == '/' or not path.startswith('/agent-setup'):
                        if path.startswith('/api'):
                            return JSONResponse(status_code=403, content={'detail': 'Viewer account has no operator access'})
                        return RedirectResponse('/agent-setup', status_code=303)
            finally:
                db.close()
        return await call_next(request)

    @app.on_event('startup')
    async def startup_event() -> None:
        if settings.AUTO_CREATE_TABLES:
            init_db()
        autopilot_service.start()
        logger.info('Kaspi SaaS Pro started')

    @app.on_event('shutdown')
    async def shutdown_event() -> None:
        await autopilot_service.stop()

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception('Unhandled error on %s', request.url.path)
        if request.url.path.startswith('/api'):
            return JSONResponse(status_code=500, content={'detail': 'Internal server error'})
        from app.web.templating import templates
        return templates.TemplateResponse('error.html', {'request': request, 'error': str(exc)}, status_code=500)

    return app

app = create_app()
