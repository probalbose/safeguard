.DEFAULT_GOAL := help
PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(VENV): ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)

.PHONY: install
install: $(VENV) ## Install the package with dev extras
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

.PHONY: install-all
install-all: $(VENV) ## Install with every optional backend (redis, kafka, otel)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[all]"

.PHONY: run
run: ## Run the API gateway on :8000 with autoreload
	$(BIN)/uvicorn safeguard.api.app:create_app --factory --reload --port 8000

.PHONY: test
test: ## Run the test suite
	$(BIN)/pytest

.PHONY: cov
cov: ## Run tests with coverage
	$(BIN)/pytest --cov=safeguard --cov-report=term-missing

.PHONY: lint
lint: ## Lint with ruff
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

.PHONY: fmt
fmt: ## Auto-format with ruff
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

.PHONY: typecheck
typecheck: ## Static type check with mypy
	$(BIN)/mypy

.PHONY: check
check: lint typecheck test ## Everything CI runs

.PHONY: infra-up
infra-up: ## Start Redis + Kafka locally
	docker compose up -d

.PHONY: infra-down
infra-down: ## Stop local infrastructure
	docker compose down -v

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf build dist *.egg-info src/*.egg-info
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
