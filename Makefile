.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

# Optional local overrides. docker compose reads .env by itself; including it here makes
# the same values visible to uv, pytest and the CLI.
-include .env
export

.PHONY: help install up down reset-db migrate psql lint fmt typecheck test test-unit test-samples
.PHONY: check samples ingest-samples bench-load fixture quirks

help: ## List the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv from uv.lock and install the git pre-commit hooks
	$(UV) sync --locked
	$(UV) run pre-commit install

up: ## Start PostgreSQL 16 (docker compose) and wait until it is healthy
	docker compose up -d --wait

down: ## Stop PostgreSQL, keeping its data volume
	docker compose down

reset-db: ## Stop PostgreSQL and delete its data volume
	docker compose down --volumes

migrate: ## Create or upgrade the ifc schema in the configured database (needs `make up`)
	$(UV) run ifc-sql migrate

psql: ## Open psql on the development database, inside the compose container
	docker compose exec db psql -U "$${POSTGRES_USER:-ifc}" -d "$${POSTGRES_DB:-ifc}"

lint: ## Lint and check formatting (ruff)
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-format and apply safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Static type check (mypy --strict on src/ and scripts/)
	$(UV) run mypy

test: ## Unit + integration tests with coverage (needs `make up`; migrates its own throwaway databases)
	$(UV) run pytest --cov --cov-report=term-missing

test-unit: ## Unit tests only, no database needed
	$(UV) run pytest tests/unit

test-samples: ## Tests on the four real Duplex files (downloads them if needed)
	$(UV) run pytest -m sample

check: lint typecheck test ## Everything CI runs

samples: ## Download the pinned, checksum-verified Duplex sample files into data/samples/
	$(UV) run python scripts/download_samples.py

ingest-samples: samples migrate ## Load the four Duplex files as ARC, MEP, ELE and PLU (needs `make up`)
	$(UV) run ifc-sql ingest data/samples/Duplex_A_20110907.ifc --discipline ARC
	$(UV) run ifc-sql ingest data/samples/Duplex_MEP_20110907.ifc --discipline MEP
	$(UV) run ifc-sql ingest data/samples/Duplex_Electrical_20121207.ifc --discipline ELE
	$(UV) run ifc-sql ingest data/samples/Duplex_Plumbing_20121113.ifc --discipline PLU

bench-load: ## Time the load stage on the MEP sample in a throwaway database (needs `make up`)
	$(UV) run python scripts/bench_load.py

fixture: ## Regenerate the synthetic test fixture tests/fixtures/revit_quirks.ifc
	$(UV) run python tests/fixtures/make_revit_quirks.py

quirks: ## Print the figures cited in docs/revit-ifc-quirks.md (needs `make samples`)
	$(UV) run python scripts/quirks_report.py
