from functools import lru_cache
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = 'Kaspi Price Manager Pro'
    APP_VERSION: str = '2.0.0'
    ENVIRONMENT: str = 'local'
    DEBUG: bool = True
    ENABLE_DOCS: bool = True
    SECRET_KEY: str = 'change-this-secret-key-in-production'
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    DATABASE_URL: str = 'sqlite:///./kaspi_saas.db'
    AUTO_CREATE_TABLES: bool = True

    REDIS_URL: str = 'redis://localhost:6379/0'
    CELERY_BROKER_URL: str = 'redis://localhost:6379/1'
    CELERY_RESULT_BACKEND: str = 'redis://localhost:6379/2'

    CORS_ORIGINS: List[str] = ['http://localhost:8000', 'http://127.0.0.1:8000']

    ADMIN_EMAIL: str = 'admin@example.com'
    ADMIN_USERNAME: str = 'admin'
    ADMIN_PASSWORD: str = 'ChangeMe123!'

    # Real Kaspi API. No mock mode.
    KASPI_BASE_URL: str = 'https://kaspi.kz/shop/api'
    KASPI_API_TOKEN: str = ''
    KASPI_MERCHANT_ID: str = ''
    KASPI_STORE_ID: str = ''
    KASPI_COMPANY_NAME: str = ''
    KASPI_DEFAULT_BRAND: str = 'NoBrand'
    KASPI_HTTP_TIMEOUT_SECONDS: int = 30
    KASPI_IMPORT_SCHEMA_PATH: str = '/products/import/schema'
    KASPI_PRODUCTS_IMPORT_PATH: str = '/products/import'
    KASPI_IMPORT_STATUS_PATH: str = '/products/import'
    KASPI_IMPORT_RESULT_PATH: str = '/products/import/result'
    # Официальный /products/import нужен для добавления/проверки товаров.
    # Прямое изменение цены через API включаем только если Kaspi выдал отдельный подтверждённый endpoint.
    KASPI_PRICE_UPDATE_FORMAT: str = 'xml_catalog'  # основной безопасный режим: xml_catalog. json_import не используем как прямую цену.
    KASPI_DIRECT_PRICE_API_ENABLED: bool = False
    KASPI_DIRECT_PRICE_UPDATE_PATH: str = ''
    KASPI_DIRECT_PRICE_UPDATE_METHOD: str = 'POST'
    # Конкуренты через публичную витрину Kaspi. Это НЕ Seller API.
    # Включено для мониторинга публичных предложений по одному товару.
    KASPI_PUBLIC_OFFERS_ENABLED: bool = True
    KASPI_PUBLIC_OFFERS_BASE_URL: str = 'https://kaspi.kz/yml/offer-view/offers'
    KASPI_PUBLIC_CITY_ID: str = '750000000'  # Алматы
    KASPI_PUBLIC_OFFERS_LIMIT: int = 10
    KASPI_PUBLIC_SORT_OPTION: str = 'PRICE'
    # Если у тебя появится официальный/партнёрский endpoint офферов, можно указать его здесь.
    KASPI_OFFERS_URL_TEMPLATE: str = ''
    PRICE_CHECK_INTERVAL_MINUTES: int = 15
    MAX_PRICE_CHANGE_PERCENT: float = 30.0
    DEFAULT_CURRENCY: str = 'KZT'

    # XML Autopilot: после первого импорта ACTIVE.xlsx сайт сам пересобирает XML-прайс.
    # Kaspi забирает ссылку /kaspi-feed/{store_id}.xml по своему расписанию.
    KASPI_AUTOPILOT_ENABLED: bool = True
    KASPI_AUTOPILOT_INTERVAL_MINUTES: int = 30
    KASPI_AUTOPILOT_STARTUP_DELAY_SECONDS: int = 25
    KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN: int = 5000
    KASPI_AUTOPILOT_WAREHOUSE_ID: str = 'PP1'
    KASPI_AUTOPILOT_UPDATE_LOCAL_PRICE: bool = True
    KASPI_AUTOPILOT_DELAY_SECONDS: float = 5.0
    KASPI_XML_REBUILD_ON_PULL: bool = True
    KASPI_XML_STALE_AFTER_MINUTES: int = 25
    KASPI_COMPETITOR_CACHE_MINUTES: int = 360
    KASPI_USE_STALE_COMPETITOR_CACHE_ON_ERROR: bool = True
    KASPI_AUTOPILOT_CONCURRENCY: int = 1
    KASPI_AUTOPILOT_ONLY_IN_STOCK: bool = True

    # Защита лимита изменений Kaspi: не более 250 изменений за 30 минут.
    # Используем запас 10 и реально выпускаем максимум 240 новых цен за окно.
    KASPI_PRICE_CHANGE_LIMIT_ENABLED: bool = True
    KASPI_PRICE_CHANGE_WINDOW_MINUTES: int = 30
    KASPI_PRICE_CHANGE_LIMIT_PER_WINDOW: int = 250
    KASPI_PRICE_CHANGE_SAFETY_RESERVE: int = 10

    KASPI_DEFAULT_STORE_AUTO_CREATE: bool = True

    SMTP_HOST: str = ''
    SMTP_PORT: int = 587
    SMTP_USER: str = ''
    SMTP_PASSWORD: str = ''
    TELEGRAM_BOT_TOKEN: str = ''
    TELEGRAM_ADMIN_CHAT_ID: str = ''

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
