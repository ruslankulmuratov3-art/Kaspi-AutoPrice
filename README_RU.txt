
Kaspi AutoPrice — DetachedInstanceError hotfix

Причина:
autopilot_service.py обращался к job.mode после закрытия SQLAlchemy Session.
На PostgreSQL/Render объект становился detached/expired.

Установка из корня проекта:

    .\.venv\Scripts\python.exe apply_hotfix.py

Проверка:

    $env:PYTHONPATH="."
    .\.venv\Scripts\python.exe -m pytest -q

Потом:

    git add .
    git commit -m "Fix detached autopilot job mode"
    git push

После деплоя:
1. Открой /automation
2. Нажми "Сбросить старое задание"
3. Нажми "Запустить"

Termux можно оставить включённым: он продолжает наполнять кэш PostgreSQL.
