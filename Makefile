.PHONY: install lint fmt typecheck test cov docs docs-serve build build-one clean all \
        test-docker test-docker-one encoding

# The workspace member that single-package targets act on.
PKG ?= validia

install:  ## Sync the dev + docs environment for the whole workspace
	uv sync --group dev --group docs
	uv run pre-commit install

lint:  ## Lint and check formatting across every member
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## Autofix and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:  ## Strict type check (one pass over the workspace)
	uv run mypy

test:  ## Run the test suite for every member
	uv run pytest

test-contract:  ## Live provider calls; needs real keys in the environment
	uv run pytest -m contract libs/franca/tests/contract

cov:  ## Run tests with coverage (fail_under=90)
	uv run pytest --cov --cov-report=term-missing

encoding:  ## Fail on any open()/read_text() missing an explicit encoding=
	PYTHONWARNDEFAULTENCODING=1 uv run pytest -q --no-cov -W error::EncodingWarning

test-docker:  ## Run the Linux scenarios CI cannot reproduce (musl, POSIX locale, mounts)
	docker compose -f docker/compose.yaml build --quiet
	@for s in glibc musl posix-locale secrets readonly-root nonroot; do \
		printf '\n=== %s ===\n' "$$s"; \
		docker compose -f docker/compose.yaml run --rm --quiet-pull "$$s" || exit 1; \
	done
	docker compose -f docker/compose.yaml down --remove-orphans >/dev/null 2>&1 || true

test-docker-one:  ## One scenario: make test-docker-one S=musl
	docker compose -f docker/compose.yaml run --rm $(S)

docs:  ## Build the docs, failing on broken references
	uv run mkdocs build --strict

docs-serve:  ## Live-reloading docs on http://127.0.0.1:8000
	uv run mkdocs serve

build:  ## Build every member into ./dist and validate the metadata
	rm -rf dist
	uv build --all-packages --out-dir dist
	uvx twine check dist/*

build-one:  ## Build a single member: make build-one PKG=validia
	uv build --package $(PKG) --out-dir dist
	uvx twine check dist/$(PKG)-*

clean:
	rm -rf dist build site .coverage .coverage.* htmlcov
	rm -rf libs/*/dist libs/*/build
	rm -rf .pytest_cache .mypy_cache .ruff_cache

all: lint typecheck cov docs build
