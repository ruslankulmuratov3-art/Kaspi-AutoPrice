from celery import Celery
from app.core.config import settings

celery_app = Celery('kaspi_saas_pro', broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND)
celery_app.conf.timezone = 'Asia/Almaty'
celery_app.conf.beat_schedule = {
    'run-auto-pricing-every-15-min': {
        'task': 'app.worker.tasks.run_auto_pricing',
        'schedule': settings.PRICE_CHECK_INTERVAL_MINUTES * 60,
    },
    'sync-products-hourly': {
        'task': 'app.worker.tasks.sync_products',
        'schedule': 60 * 60,
    },
}
