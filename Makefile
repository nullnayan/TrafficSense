PYTHON ?= python3.11
.PHONY: setup up down api health trigger nlp spatial analytics scout assistant eval migrate test lint ui demo summary
setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt
	@test -f .env || cp .env.example .env
up:
	docker compose up -d --wait
down:
	docker compose down
api:
	.venv/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
health:
	curl --fail http://127.0.0.1:8000/health
trigger:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/ingest/trigger
nlp:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/nlp/process
spatial:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/spatial/process
analytics:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/analytics/process
scout:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/scout/evaluate -H 'Content-Type: application/json' -d '{"origin":"Clock Tower","destination":"Laxman Jhula","include_fixtures":true}'
assistant:
	curl --fail -X POST http://127.0.0.1:8000/api/v1/assistant/query -H 'Content-Type: application/json' -d '{"query":"Are there active accidents near Clock Tower?","include_fixtures":true}'
eval:
	.venv/bin/python -m backend.nlp.evaluate
migrate:
	docker compose exec -T trafficsense-postgis sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -f /docker-entrypoint-initdb.d/migrate.psql'
test:
	.venv/bin/python -m pytest -q
lint:
	.venv/bin/python -m ruff check backend tests scripts training/train_setfit.py
ui:
	cd frontend && npm install && npm run dev
demo:
	.venv/bin/python -m backend.streaming.demo_pipeline
summary:
	.venv/bin/python -m backend.evaluation.summary
