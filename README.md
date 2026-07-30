# Kaspi AutoPrice v3

Профессиональный FastAPI-сервис для безопасного формирования полного XML-прайса Kaspi.

## Что исправлено

- PostgreSQL через `DATABASE_URL`; данные не зависят от файловой системы Render.
- Задания автопилота сохраняются в БД и переживают restart/redeploy.
- Одновременно на магазин выполняется одно задание.
- `Stop` ставит задачу на паузу после текущего товара; `Resume` продолжает с курсора. Решения по уже обработанным товарам хранятся в `autopilot_job_items` и не теряются после restart.
- HTTP 405/429 открывает circuit breaker: новые запросы прекращаются до cooldown.
- Конкуренты хранятся в PostgreSQL-кэше. При сбое используется разрешённый кэш или прежняя цена.
- XML всегда строится из полного активного каталога.
- Кандидатная XML-версия проверяется до публикации. Пустая или резко уменьшившаяся версия получает `rejected`, прежняя остаётся активной.
- XML хранится в PostgreSQL и отдаётся сразу, без запуска расчёта при запросе Kaspi.
- Прямой API изменения цены выключен: официальный токен используется только для документированных API-операций.
- Лимит изменения цен конфигурируется и сохраняется в PostgreSQL; лишние изменения переходят в очередь.

## Официальная логика Kaspi

Открытый Kaspi Гид описывает API-токен для добавления товаров и работы с заказами. Для массового автоматического изменения цен и остатков используется XML-прайс по публичной HTTP/HTTPS-ссылке. Поэтому основной режим проекта — XML. `KASPI_DIRECT_PRICE_API_ENABLED` нельзя включать без официального endpoint, предоставленного Kaspi.

## Локальный запуск Windows

```powershell
cd C:\Users\Ruslan2\Downloads\kaspi_saas_real
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe scripts\seed.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Открыть: `http://127.0.0.1:8000`

## Тесты

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pytest -q
```

Сетевые тесты используют mock и не отправляют массовые запросы в Kaspi. Текущий комплект: 9 тестов.

## Render

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Обязательно добавьте:

```text
DATABASE_URL=<Internal Database URL Render Postgres>
PUBLIC_BASE_URL=https://kaspi-autoprice.onrender.com
SECRET_KEY=<случайная длинная строка>
ADMIN_PASSWORD=<новый пароль>
```

Остальные параметры возьмите из `.env.example`.

## Безопасность

- `.env`, SQLite, архивы и ZIP исключены из Git.
- Токены и пароли не выводятся в интерфейсе.
- Проект не использует Selenium, cookies кабинета, CAPTCHA bypass или маскировку под мобильное приложение.
- HTTP/HTML ошибки сокращаются перед сохранением.

## Git

```powershell
git add .
git status
git commit -m "Stable Kaspi AutoPrice v3"
git push
```
