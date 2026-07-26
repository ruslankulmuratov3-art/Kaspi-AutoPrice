from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.web.router import web_router
from app.core.config import settings
from app.core.database import init_db
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
