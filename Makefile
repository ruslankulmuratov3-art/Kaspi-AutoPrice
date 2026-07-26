install:
	python -m pip install -r requirements.txt

seed:
	python scripts/seed.py

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A app.worker.celery_app.celery_app worker --loglevel=info

beat:
	celery -A app.worker.celery_app.celery_app beat --loglevel=info

test:
	pytest -q

docker-up:
	docker compose up --build
