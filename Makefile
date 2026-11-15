.PHONY: up down check test accept

PYTHON := uv run --no-sync python
PYTEST := uv run --no-sync pytest

up:
	docker compose -f deploy/compose/docker-compose.lightweight.yaml up -d --build

down:
	docker compose -f deploy/compose/docker-compose.lightweight.yaml down

check:
	uv run --no-sync ruff check src/ tests/ workflows/ scripts/
	uv run --no-sync mypy src/ workflows/
	$(PYTHON) scripts/check_architecture.py
	$(PYTHON) scripts/scan_cleartext_logs.py

test: check
	$(PYTEST) tests/ -v

accept: check
	@if [ -z "$(WP)" ]; then echo "Fehler: Bitte WP=LWP-xx angeben (z. B. make accept WP=lwp_05)"; exit 1; fi
	$(PYTEST) tests/ -k "$(WP)" -v --tb=short
