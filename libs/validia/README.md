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
```

Requires Python 3.12+. Fully typed; ships a `py.typed` marker.

## Usage

```python
import asyncio

import validia


async def main() -> None:
    print(validia.__version__)


asyncio.run(main())
```

## Development

This package is a member of the [validia.dev workspace](https://github.com/izmailov-labs/validia).
Development commands run from the repository root, not from this directory:

```bash
make install     # sync dev + docs groups, install pre-commit hooks
make all         # lint, typecheck, coverage, docs, build
```

See the [contributing notes in the docs](https://validia.dev/getting-started/).

## Documentation

<https://validia.dev>

## License

MIT — see [LICENSE](LICENSE).
