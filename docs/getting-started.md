# Getting started

## Using validia

```bash
uv add validia
```

```python
import validia

print(validia.__version__)
```

## Local development

The repository is a [uv](https://docs.astral.sh/uv/) workspace (0.12 or newer). Each
published package lives under `libs/`, and every command below runs from the repository
root and covers all of them at once.

```bash
git clone https://github.com/izmailov-labs/validia
cd validia
make install     # sync dev + docs groups, install pre-commit hooks
```

```
validia.dev/
├── pyproject.toml        # workspace root: shared tooling, never published
└── libs/
    └── validia/          # the package on PyPI
        ├── pyproject.toml
        ├── src/validia/
        └── tests/
```

Common tasks:

| Command | What it does |
| --- | --- |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Autofix and format |
| `make typecheck` | `mypy` in strict mode |
| `make test` | Run the test suite |
| `make cov` | Tests with coverage (fails under 90%) |
| `make docs` | Build these docs with `--strict` |
| `make build` | Build every member's sdist + wheel and validate metadata |
| `make build-one PKG=validia` | Build a single member |
| `make all` | Everything CI runs |

Tests run under `pytest-asyncio` in auto mode, so `async def test_*` functions
need no decorator.
