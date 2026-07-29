Kaspi Official API Safe Patch

Что исправляет:
1) Не имитирует прямое изменение цены через /products/import.
2) Direct API mode выключен, пока Kaspi не даст официальный endpoint изменения цены.
3) XML Mode остаётся основным безопасным способом.
4) При ошибках конкурентов 405/429 цена не меняется вслепую.
5) Если есть сохранённый кэш конкурентов, он используется безопасно.
6) Если автопилот остановлен, новый частичный XML не сохраняется.
7) Если XML ещё не создан, /kaspi-feed/{store_id}.xml отдаёт 503, а не пустой <offers/>.
8) Поставлены безопасные дефолты: concurrency=1, delay=5s, cache=360min, offers_limit=10.

После установки:
- git add .
- git commit -m "Fix official Kaspi API and safe XML autopilot"
- git push

Запуск локально:
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe scripts\seed.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
