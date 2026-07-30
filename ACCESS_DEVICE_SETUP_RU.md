# Регистрация, Google и доверенные устройства

## Что изменилось

- Регистрация только по коду `USR`, созданному владельцем.
- Вход через email/пароль.
- Опциональный вход через Google.
- Роль нового пользователя — `viewer`: только страница подключения устройства.
- Одноразовые коды `DEV` для подключения компьютера или Android-агента.
- Отдельный токен для каждого устройства; в PostgreSQL хранится только хэш.
- Админ-панель показывает пользователей, коды и устройства.
- Администратор может отключить, удалить устройство или пользователя.
- Отключённый пользователь автоматически теряет доступ через все свои устройства.

## 1. Установка

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe -m pytest -q
```

Ожидается: `19 passed`.

## 2. Render

Минимальные переменные:

```env
PUBLIC_BASE_URL=https://kaspi-autoprice.onrender.com
REGISTRATION_ENABLED=true
LOCAL_AGENT_ENABLED=true
LOCAL_AGENT_ALLOW_LEGACY_TOKEN=false
KASPI_PUBLIC_OFFERS_ENABLED=false
```

Start Command остаётся прежним:

```text
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## 3. Создание аккаунта клиента

1. Владелец входит в `/admin`.
2. Нажимает «Создать код» в блоке регистрации.
3. Передаёт пользователю код вида `USR-XXXX-XXXX-XXXX`.
4. Пользователь открывает `/register`.
5. Вводит код, email и пароль либо продолжает через Google.

Полный код показывается только один раз. В базе хранится его хэш.

## 4. Создание устройства

1. В `/admin` выбрать пользователя.
2. Нажать «Создать код устройства».
3. Передать код `DEV-XXXX-XXXX-XXXX` владельцу устройства.
4. На устройстве выполнить первое подключение:

```powershell
$env:RENDER_BASE_URL="https://kaspi-autoprice.onrender.com"
.\.venv\Scripts\python.exe scripts\local_agent.py --pair-code "DEV-XXXX-XXXX-XXXX" --device-name "Office PC" --fast
```

После успешного подключения токен сохраняется в:

```text
~/.kaspi_autoprice_agent.json
```

Дальше достаточно:

```powershell
.\.venv\Scripts\python.exe scripts\local_agent.py --fast
```

Сброс локального устройства:

```powershell
.\.venv\Scripts\python.exe scripts\local_agent.py --reset-config
```

## 5. Android

Простое открытие сайта в Chrome не превращает телефон в агент: браузер может блокировать прямой запрос и останавливать вкладку в фоне. На Android нужен Python/Termux или отдельное приложение, которое запускает `scripts/local_agent.py` с добровольного согласия владельца устройства.

## 6. Google

В Google Cloud создайте OAuth Client типа Web application.

Authorized redirect URI:

```text
https://kaspi-autoprice.onrender.com/auth/google/callback
```

Render variables:

```env
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_REDIRECT_URI=https://kaspi-autoprice.onrender.com/auth/google/callback
```

Без этих трёх значений кнопка Google скрыта, а email/пароль продолжает работать.

## 7. Управление

В `/admin` доступны:

- выпуск и закрытие кодов;
- включение и отключение аккаунтов;
- изменение роли;
- просмотр зарегистрированных устройств;
- последняя активность, IP и результат обработки;
- отключение и удаление устройства.

Для ускорения можно подключить несколько доверенных устройств на разных сетях. Каждый агент работает последовательно и останавливается при `403`, `405`, `429` или HTML вместо JSON. Задержку ниже 3 секунд ставить не нужно.
