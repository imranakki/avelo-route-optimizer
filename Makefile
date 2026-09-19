# Dev shortcuts. Run `make` alone to see what is available.
VENV := ./.venv/bin

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Create the venv and install everything
	python3 -m venv .venv && $(VENV)/pip install -q -e ".[dev]"

.PHONY: test
test: ## Run the offline test suite
	$(VENV)/pytest

.PHONY: cache
cache: ## Build the full offline cache for Québec City (matrices, elevation, polylines)
	$(VENV)/python scripts/build_cache.py

.PHONY: eval
eval: ## Run the evaluation over live data and write docs/evaluation.md
	$(VENV)/python scripts/evaluate.py

.PHONY: live
live: ## Run integration tests against the real àVélo feed
	$(VENV)/pytest -m integration

.PHONY: lint
lint: ## Lint and type-check
	$(VENV)/ruff check src tests && $(VENV)/mypy src

.PHONY: fmt
fmt: ## Auto-format
	$(VENV)/ruff format src tests && $(VENV)/ruff check --fix src tests

.PHONY: run
run: ## Start the API at http://127.0.0.1:8000/docs
	$(VENV)/uvicorn avelo.api.app:app --reload

.PHONY: check
check: lint test ## Everything CI runs
