.PHONY: install lint fmt typecheck test cov encoding docs docs-serve build clean all

install:  ## Sync the dev + docs environment and install the pre-commit hooks
	uv sync --group dev --group docs
	uv run pre-commit install

lint:  ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## Autofix and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:  ## Strict type check (src and tests)
	uv run mypy

test:  ## Run the test suite
	uv run pytest

cov:  ## Run tests with coverage (fail_under=90)
	uv run pytest --cov --cov-report=term-missing

encoding:  ## Fail on any open()/read_text() missing an explicit encoding=
	PYTHONWARNDEFAULTENCODING=1 uv run pytest -q --no-cov -W error::EncodingWarning

docs:  ## Build the docs, failing on broken references
	uv run mkdocs build --strict

docs-serve:  ## Live-reloading docs on http://127.0.0.1:8000
	uv run mkdocs serve

build:  ## Build the wheel and sdist into ./dist and validate the metadata
	rm -rf dist
	uv build --out-dir dist
	uvx twine check dist/*

clean:
	rm -rf dist build site .coverage .coverage.* htmlcov
	rm -rf .pytest_cache .mypy_cache .ruff_cache

all: lint typecheck cov docs build
