.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv

# Optional local overrides. docker compose reads .env by itself; including it here makes
# the same values visible to uv, pytest and the CLI.
-include .env
export

.PHONY: help install up down reset-db migrate lint fmt typecheck test test-unit check samples

help: ## List the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

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

check: lint typecheck test ## Everything CI runs

samples: ## Download the pinned, checksum-verified Duplex sample files into data/samples/
	$(UV) run python scripts/download_samples.py
