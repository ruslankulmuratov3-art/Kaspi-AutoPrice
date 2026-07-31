# Kaspi AutoPrice v5.1

FastAPI-сервис для безопасного расчёта цен и публикации полного XML-каталога Kaspi.

## Главное в v5.1

- Исправлена локальная ошибка SQLite `database is locked` при финальном сохранении XML: версия XML и завершение задания теперь записываются одной SQLAlchemy Session/транзакцией.
- Для SQLite включены `WAL`, `busy_timeout=60s` и короткие упорядоченные записи.
- Добавлено примерное оставшееся время; браузер уточняет скорость по фактическому движению прогресса.
- Огромный SQL/XML больше не выводится в красной ошибке — полная трассировка остаётся только в логах.
- Исправлен `DetachedInstanceError`: фоновые задачи получают только `job_id`, а ORM-объекты не используются после закрытия SQLAlchemy Session.
- Ошибка worker переводит задание в `error`; зависшее задание определяется по heartbeat и не остаётся вечным `running`.
- Реальное восстановление продолжает работу с сохранённого курсора и показывает уведомление только один раз.
- Результат телефона/компаньона рассчитывает **один товар сразу**, без повторного прохода по всем товарам.
- Чтение готового PostgreSQL-кэша не использует сетевую задержку.
- Полный XML пересобирается с debounce, валидируется и активируется атомарно.
- Пустой, частичный, дублированный или содержащий нулевую цену XML не публикуется.
- Production URL отделён от локального `127.0.0.1`.
- Статистика «Изменено / Без изменений / Ошибки / В очереди» открывает подробный список причин.
- XML History имеет нормальное пустое состояние, версии, сравнение и журнал запросов.
- Убраны из интерфейса «Устройства», «Админ», регистрация, Google-вход и DEV/USR-коды.
- Добавлена временная ссылка «Ускорить проверку через телефон» с явным согласием пользователя.
- Интерфейс использует Manrope, а короткие крупные заголовки — Unbounded.

## Важное ограничение браузера

Браузер не выдаёт отдельное разрешение «использовать IP». Страница сначала делает один CORS-тест. Если Kaspi запрещает cross-origin запрос, браузерный режим честно останавливается и предлагает companion-скрипт. Проект не обходит CORS, CAPTCHA или антибот-защиту и не использует cookies личного кабинета.

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

Локальный XML предназначен только для проверки. В кабинет Kaspi добавляется:

```text
https://kaspi-autoprice.onrender.com/kaspi-feed/1.xml
```

## Телефон по одной ссылке

1. Откройте `Автопилот`.
2. Нажмите `Создать ссылку для телефона`.
3. Откройте ссылку на телефоне.
4. Прочитайте объяснение и нажмите `Начать проверку`.
5. Если CORS разрешён, прогресс виден прямо в браузере.
6. Если CORS заблокирован, страница покажет companion-команду с той же временной сессией — DEV-код вводить не нужно.

Companion fallback:

```powershell
$env:RENDER_BASE_URL="https://kaspi-autoprice.onrender.com"
.\.venv\Scripts\python.exe scripts\local_agent.py --helper-url "ВРЕМЕННАЯ_ССЫЛКА" --fast
```

На Android с Python/Termux команда аналогичная:

```bash
export RENDER_BASE_URL="https://kaspi-autoprice.onrender.com"
python scripts/local_agent.py --helper-url "ВРЕМЕННАЯ_ССЫЛКА" --fast
```

## Render

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Минимальные Environment Variables:

```text
ENVIRONMENT=production
DATABASE_URL=<Internal Database URL Render PostgreSQL>
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

`DATABASE_URL` должен быть Internal Database URL PostgreSQL. Локальная SQLite и Render PostgreSQL — разные базы и не синхронизируются автоматически.

## Проверка

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m compileall -q app scripts
.\.venv\Scripts\python.exe -m pytest -q
```

Проверено: 25 тестов. Сетевые запросы в тестах замоканы.

## Git и деплой

```powershell
git add .
git status
git commit -m "Kaspi AutoPrice v5.1 SQLite, XML and ETA fix"
git push
```

Render автоматически начнёт deploy. После деплоя:

1. Откройте `/automation`.
2. Если старое задание действительно зависло, нажмите `Сбросить зависшее задание`.
3. Создайте полный XML и проверьте количество товаров.
4. Откройте production XML.
5. Убедитесь, что XML содержит полный каталог и положительные цены.
6. Создайте ссылку для телефона и выполните CORS-тест.

## Что зависит не от проекта

- Browser helper зависит от CORS Kaspi.
- Запросы публичной витрины могут получить 403/405/429; проект останавливается безопасно и не обходит ограничения.
- Публикация XML не доказывает мгновенное применение цены: Kaspi забирает и обрабатывает XML по своему расписанию.
