Kaspi AutoPrice multi-agent hotfix

Исправляет ошибку:
AttributeError: 'CompetitorService' object has no attribute 'AGENT_SOURCE'

Причина: основной multi-agent patch не включал обновленный app/services/competitor_service.py.

Скопируйте содержимое этой папки в корень проекта с заменой файлов.
После установки запустите:
  $env:PYTHONPATH="."
  .\.venv\Scripts\python.exe scripts\migrate.py
  .\.venv\Scripts\python.exe -m pytest -q

Ожидается: 12 passed.
