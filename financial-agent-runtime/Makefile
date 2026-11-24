.PHONY: up down test lint worker api seed-finance finance-benchmark
up:
	docker compose up --build

down:
	docker compose down

test:
	pytest -q

lint:
	ruff check src tests

api:
	uvicorn agent_runtime.api:app --reload

worker:
	python -m agent_runtime.worker

seed-finance:
	python scripts/seed_financial_demo.py

finance-benchmark:
	python scripts/run_finance_benchmark.py
