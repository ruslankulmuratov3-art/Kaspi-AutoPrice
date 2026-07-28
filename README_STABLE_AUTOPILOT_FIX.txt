Kaspi AutoPrice stable autopilot fix

Что исправлено:
- DATABASE_URL нормально работает с Render PostgreSQL.
- create_all больше не сбрасывает базу; seed.py только создаёт/проверяет admin и дефолтный магазин, если магазинов нет.
- Добавлен безопасный schema upgrade: новые колонки добавляются без удаления данных.
- ACTIVE.xlsx импортируется один раз, товары сохраняются в базе, старые товары не удаляются.
- Повторный импорт обновляет товары и помечает отсутствующие в новом ACTIVE.xlsx.
- Поиск работает по названию, SKU, product_id, бренду и ссылке через casefold.
- XML сохраняется в файл и в PostgreSQL TaskLog, чтобы история не пропадала после redeploy.
- Архив Excel сохраняет копию в PostgreSQL TaskLog, чтобы скачать файл повторно даже после redeploy.
- Автопилот считает XML в фоне быстрее: кэш конкурентов + безопасная параллельность 1-5.
- Добавлена кнопка остановки автопилота.
- Мобильный интерфейс доработан: меню, формы, карточки, кнопки, таблицы.

ВАЖНО:
На Render обязательно создай PostgreSQL и добавь DATABASE_URL в Environment Variables.
Без DATABASE_URL Render будет использовать SQLite внутри контейнера, и данные могут пропадать после redeploy/restart.

Render env variables минимум:
PYTHON_VERSION=3.13.7
DATABASE_URL=<External Database URL from Render PostgreSQL>
SECRET_KEY=<любая длинная строка>
KASPI_API_TOKEN=<токен>
KASPI_MERCHANT_ID=30140513
KASPI_STORE_ID=30140513
KASPI_COMPANY_NAME=EXCLUSIVE_KZ
KASPI_DEFAULT_BRAND=NoBrand
KASPI_PUBLIC_OFFERS_ENABLED=true
KASPI_PUBLIC_CITY_ID=750000000
KASPI_PUBLIC_OFFERS_LIMIT=30
KASPI_PUBLIC_SORT_OPTION=PRICE
KASPI_AUTOPILOT_ENABLED=true
KASPI_AUTOPILOT_INTERVAL_MINUTES=60
KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN=5000
KASPI_AUTOPILOT_CONCURRENCY=3
KASPI_COMPETITOR_CACHE_MINUTES=15
KASPI_XML_REBUILD_ON_PULL=true
KASPI_XML_STALE_AFTER_MINUTES=55

Start command:
PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
