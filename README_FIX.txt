Исправляет ошибку:
AttributeError: 'KaspiClient' object has no attribute 'parse_offers_payload'

Файл заменяется:
app/services/kaspi_client.py

После копирования:
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pytest -q

Ожидается: 15 passed.
