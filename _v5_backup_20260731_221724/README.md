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

Сетевые тесты используют mock и не отправляют массовые запросы в Kaspi. Текущий комплект: 19 тестов.

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

## Регистрация и доверенные устройства

Новые аккаунты создаются только по одноразовому коду `USR`, который владелец выпускает в `/admin`.
Поддерживаются email/пароль и Google OpenID Connect. Новому пользователю назначается роль `viewer`: он видит только страницу своих устройств и не получает доступ к магазинам, товарам и настройкам цен.

Для подключения агента владелец создаёт код `DEV`, привязанный к конкретному пользователю. Агент обменивает этот код на отдельный токен устройства; в базе хранится только SHA-256 хэш токена. В админ-панели видно имя, пользователя, платформу, IP, последнюю активность и статистику. Устройство можно отключить или удалить.

Render environment:

```env
REGISTRATION_ENABLED=true
LOCAL_AGENT_ENABLED=true
LOCAL_AGENT_ALLOW_LEGACY_TOKEN=false
KASPI_PUBLIC_OFFERS_ENABLED=false
PUBLIC_BASE_URL=https://kaspi-autoprice.onrender.com
```

Первое подключение устройства:

```powershell
$env:RENDER_BASE_URL="https://kaspi-autoprice.onrender.com"
.\.venv\Scripts\python.exe scripts\local_agent.py --pair-code "DEV-XXXX-XXXX-XXXX" --device-name "Office PC" --fast
```

Следующие запуски:

```powershell
.\.venv\Scripts\python.exe scripts\local_agent.py --fast
```

Токен сохраняется локально в `~/.kaspi_autoprice_agent.json`. Обычное открытие сайта в браузере телефона агент не запускает; на Android нужен Python/Termux или отдельное клиентское приложение.

### Google вход

Добавьте Web OAuth Client в Google Cloud и зарегистрируйте redirect URI:

```text
https://kaspi-autoprice.onrender.com/auth/google/callback
```

Render variables:

```env
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=https://kaspi-autoprice.onrender.com/auth/google/callback
```

Подробная пошаговая инструкция: `ACCESS_DEVICE_SETUP_RU.md`.
