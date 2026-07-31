# Render deploy — Kaspi AutoPrice v5.1

## Build

```text
pip install -r requirements.txt
```

## Start

```text
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Обязательные переменные

```text
ENVIRONMENT=production
DATABASE_URL=<Internal Database URL PostgreSQL>
PUBLIC_BASE_URL=https://kaspi-autoprice.onrender.com
SECRET_KEY=<длинная случайная строка>
ADMIN_PASSWORD=<новый пароль>
KASPI_API_TOKEN=<секрет>
KASPI_MERCHANT_ID=30140513
KASPI_STORE_ID=30140513
KASPI_COMPANY_NAME=EXCLUSIVE_KZ
KASPI_DEFAULT_BRAND=NoBrand
KASPI_DIRECT_PRICE_API_ENABLED=false
KASPI_PRICE_UPDATE_FORMAT=xml_catalog
KASPI_PUBLIC_OFFERS_ENABLED=false
LEGACY_ACCESS_UI_ENABLED=false
REGISTRATION_ENABLED=false
HELPER_BROWSER_ENABLED=true
HELPER_SESSION_EXPIRE_MINUTES=180
HELPER_SESSION_BATCH_SIZE=25
HELPER_XML_DEBOUNCE_SECONDS=20
KASPI_XML_REBUILD_ON_PULL=false
```

После deploy откройте `/automation`, затем `/xml-history`, создайте полный XML и проверьте production URL.

## Важно после изменения локального кода

Render не видит локальные файлы автоматически. После тестов обязательно выполните `git add .`, `git commit` и `git push`, затем дождитесь нового deploy. Локальная SQLite и production PostgreSQL содержат разные данные.
