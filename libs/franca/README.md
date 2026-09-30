# franca

[![CI](https://github.com/izmailov-labs/validia/actions/workflows/ci.yml/badge.svg)](https://github.com/izmailov-labs/validia/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/franca.svg)](https://pypi.org/project/franca/)
[![Python versions](https://img.shields.io/pypi/pyversions/franca.svg)](https://pypi.org/project/franca/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

One request shape for every LLM wire dialect.

A *lingua franca* for model APIs. You build one request, and franca decides which wire
shape the target model actually speaks, translates into it, and translates the answer
back. Five chat dialects, plus image and video wires, behind one intermediate
representation.

Three axes are kept separate, because they vary independently:

| Axis | Meaning | Owns |
| --- | --- | --- |
| **Provider** | who you authenticate with | endpoint rows, settings, key lookup |
| **Dialect** | the shape of the bytes | the adapter, the typed request, stream mapping |
| **Model** | the weights | the profile row that adapters read |

That separation is the point. A compatibility surface that serves one provider's wire
shape under another's host, and remaps model names on the way, needs no special case
anywhere else.

> **Status:** `0.1.0` — scaffolding. No public API yet.

## Install

```bash
uv add franca
```

Requires Python 3.12+. Fully typed; ships a `py.typed` marker.

## Development

This package is a member of the [validia.dev workspace](https://github.com/izmailov-labs/validia).
Development commands run from the repository root, not from this directory:

```bash
make install     # sync dev + docs groups, install pre-commit hooks
make all         # lint, typecheck, coverage, docs, build
```

## License

MIT — see [LICENSE](LICENSE).
