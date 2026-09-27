AS_OF ?= 2026-09-25

.PHONY: install lint test run report dashboard ddl up down clean

install:
	pip install -e ".[dev,dashboard]"

lint:
	ruff check . && mypy src

test:
	pytest --cov=quantrisk --cov-report=term-missing

run:
	quantrisk --plain-logs run --as-of $(AS_OF) --with-sample

dashboard:
	streamlit run dashboards/app.py

ddl:
	quantrisk init-db --ddl > sql/migrations/001_init.sql

up:
	docker compose up --build

down:
	docker compose down -v

clean:
	rm -rf data/quantrisk.db data/raw reports/*.html reports/20*
