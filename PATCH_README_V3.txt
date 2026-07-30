Kaspi AutoPrice v3 — стабильный патч

Установка в существующий проект (PowerShell):

$zip = Get-ChildItem "$env:USERPROFILE\Downloads\kaspi_autoprice_v3_stable_patch*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Remove-Item "$env:USERPROFILE\Downloads\kaspi_autoprice_v3_stable_patch_extract" -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $zip.FullName -DestinationPath "$env:USERPROFILE\Downloads\kaspi_autoprice_v3_stable_patch_extract" -Force
Copy-Item "$env:USERPROFILE\Downloads\kaspi_autoprice_v3_stable_patch_extract\*" -Destination "." -Recurse -Force

Локальный запуск:

$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe scripts\seed.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

Тесты:

$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pytest -q

Render start command:
PYTHONPATH=. python scripts/migrate.py && PYTHONPATH=. python scripts/seed.py && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT

После проверки:
git add .
git status
git commit -m "Stable Kaspi AutoPrice v3"
git push

Не отправляйте токены и DATABASE_URL в чат. Они добавляются только в Render Environment.
