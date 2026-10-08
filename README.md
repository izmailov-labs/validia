# validia

[![CI](https://github.com/izmailov-labs/validia/actions/workflows/ci.yml/badge.svg)](https://github.com/izmailov-labs/validia/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/validia.svg)](https://pypi.org/project/validia/)
[![Python versions](https://img.shields.io/pypi/pyversions/validia.svg)](https://pypi.org/project/validia/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A universal, async-first evaluation framework for LLM systems.

validia aims to cover the full evaluation spectrum under one set of primitives:

1. **Prompt evaluation** — scoring and regression-testing prompts against held-out sets
2. **Tool selection** — whether the right tool was chosen, with the right arguments
3. **Agent evaluation** — judging completed tasks rather than individual turns
4. **Agent type** — comparing agent architectures against the same workload

> **Status:** `0.1.0` — scaffolding is in place, the public API is not yet defined.

## Install

```bash
uv add validia
# or
pip install validia
```

Requires Python 3.12+. Fully typed; ships a `py.typed` marker.

validia is built on two sibling packages, installed with it:

| Package | What it is |
| --- | --- |
| [`franca`](https://github.com/izmailov-labs/franca) | The model communication layer: one request shape for every LLM wire dialect, with transports, adapters, streaming and a model registry. |
| [`whence`](https://github.com/izmailov-labs/whence) | Typed configuration that remembers where it came from: layered loading from env, `.env`, TOML, JSON, YAML and more, with full provenance. |

## Usage

```python
import asyncio

import validia


async def main() -> None:
    print(validia.__version__)


asyncio.run(main())
```

## Development

A single-package repository managed with [uv](https://docs.astral.sh/uv/) (0.12 or newer).
Everything runs from the repository root.

```bash
git clone https://github.com/izmailov-labs/validia
cd validia
make install     # sync dev + docs groups, install pre-commit hooks
make all         # lint, typecheck, coverage, docs, build
```

| Command | What it does |
| --- | --- |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Autofix and format |
| `make typecheck` | `mypy` in strict mode, over `src/` and `tests/` |
| `make test` | `pytest` |
| `make cov` | Tests with coverage (fails under 90%) |
| `make encoding` | Fail on any read missing an explicit `encoding=` |
| `make docs` | Build the docs site with `--strict` |
| `make build` | Build the wheel and sdist into `./dist` and validate the metadata |

Lint, type, test and coverage configuration lives in `pyproject.toml` alongside the packaging
metadata; there is no second config file.

### Working against an unreleased franca or whence

Both dependencies resolve from PyPI. To develop validia against a local checkout of one of
them, point uv at it for the session and do not commit the result:

```bash
uv add --editable ../franca      # or ../whence
# ... work ...
git checkout pyproject.toml uv.lock
```

## Contributing

Pull requests are welcome. Fork the repository, branch off `main`, and open the PR against
`main`. CI runs on every pull request and every job is required, so run `make all` before
pushing: it is the local mirror of the CI gate, and a green run here is the cheapest way to
avoid a red one there. The pre-commit hooks `make install` sets up cover ruff and the lockfile
on the way in; mypy is deliberately not among them and is gated in CI instead, because it is
slow enough that people start reaching for `--no-verify`.

What CI adds on top of `make all`:

| Job | What it catches |
| --- | --- |
| `test` | The real matrix: Linux, macOS and Windows, at both ends of the supported Python range |
| `encoding` | Any `open()` or `read_text()` missing an explicit `encoding=` |
| `minimums` | Dependency floors that are declared but only ever tested at their latest versions |
| `build` | Broken packaging, a missing `py.typed`, a wheel that does not import |

The Python 3.15 leg is advisory until 3.15.0 ships; everything else must be green.

### What a reviewable PR looks like

- **A changelog entry** under `## [Unreleased]` in `CHANGELOG.md`, using the Keep a Changelog
  sections already in the file. Versions are bumped at release time, not here.
- **Annotated code, tests included.** mypy runs `strict = true` over `src/` and `tests/` alike,
  and suppressions are specific: `# type: ignore[code]`, never bare.
- **Docstrings on public API.** The docs site generates its reference from them, and ruff's `D`
  rules (google convention) enforce them in `src/`.
- **A new runtime dependency is a decision, not a detail** — every consumer inherits it. Open an
  issue before the PR. Dev tooling goes in `[dependency-groups]`, which is never published, and
  an optional feature goes behind an extra.
- **`uv.lock` committed** whenever a dependency changes; the `uv-lock` hook fails on drift.

Anything large enough to have a design is worth an issue before the PR; small fixes can go
straight to one.

## Releasing

One package, so tags are plain:

```
v<version>        e.g. v0.1.0
```

The release workflow verifies the tag against `pyproject.toml`, builds, and publishes through
PyPI Trusted Publishing. The trusted publisher must be configured on PyPI before the first tag.

## Documentation

<https://validia.dev>

## License

MIT — see [LICENSE](LICENSE).
