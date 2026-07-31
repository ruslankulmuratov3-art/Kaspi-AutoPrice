# Render deployment

## Build

```text
pip install -r requirements.txt
```

## Start

```text
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Required Environment Variables

```text
PYTHON_VERSION=3.13.7
ENVIRONMENT=production
DEBUG=false
DATABASE_URL=<Render Postgres Internal URL>
PUBLIC_BASE_URL=https://kaspi-autoprice.onrender.com
SECRET_KEY=<long random secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<new password>
KASPI_API_TOKEN=<secret token>
KASPI_MERCHANT_ID=30140513
KASPI_STORE_ID=30140513
KASPI_COMPANY_NAME=EXCLUSIVE_KZ
KASPI_AUTOPILOT_WAREHOUSE_ID=PP1
KASPI_XML_REBUILD_ON_PULL=false
KASPI_DIRECT_PRICE_API_ENABLED=false
```

Then add the remaining values from `.env.example`.
