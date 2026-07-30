KASPI AUTOPRICE — MULTI-AGENT FAST PATCH

Устанавливать после завершения текущего расчёта.
Патч добавляет несколько доверенных устройств, аренду заданий без дублей и быстрый пересчёт партиями.

После копирования файлов:
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe -m pytest -q

Ожидается: 16 passed.
