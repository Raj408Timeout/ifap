BACKEND := backend
VENV    := $(BACKEND)/.venv/bin

.PHONY: install dataset format lint typecheck test check api web up down

install:            ## Create venv and install backend + frontend deps
	python3.13 -m venv $(BACKEND)/.venv
	$(VENV)/pip install -e "$(BACKEND)[dev]"
	cd frontend && npm install

dataset:            ## Regenerate the 200-question sample dataset
	cd $(BACKEND) && .venv/bin/python scripts/build_sample_dataset.py

format:
	cd $(BACKEND) && .venv/bin/black src tests scripts && .venv/bin/ruff check --fix src tests scripts

lint:
	cd $(BACKEND) && .venv/bin/black --check src tests scripts && .venv/bin/ruff check src tests scripts && .venv/bin/pylint src tests

typecheck:
	cd $(BACKEND) && .venv/bin/pyright
	cd frontend && npm run typecheck

test:
	cd $(BACKEND) && .venv/bin/pytest

check: lint typecheck test   ## Full quality gate (same as CI)

api:                ## Run the API locally (SQLite + embedded Chroma)
	cd $(BACKEND) && .venv/bin/uvicorn ifap.main:app --reload --port 8000

web:                ## Run the Next.js UI locally
	cd frontend && npm run dev

up:                 ## Full stack: Postgres + Chroma + API + UI
	docker compose up --build

down:
	docker compose down
