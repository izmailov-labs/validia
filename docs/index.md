# validia

A universal, async-first evaluation framework for LLM systems.

validia aims to cover the full evaluation spectrum under one set of primitives:

1. **Prompt evaluation** — scoring and regression-testing prompts against held-out sets
2. **Tool selection** — whether the right tool was chosen, with the right arguments
3. **Agent evaluation** — judging completed tasks rather than individual turns
4. **Agent type** — comparing agent architectures against the same workload

!!! note "Early scaffolding"
    `validia` is at `0.1.0` and the public API is not yet defined. This site is
    generated from the docstrings in `src/validia/`, so it fills in as the library does.

## Install

```bash
uv add validia
# or
pip install validia
```

Requires Python 3.12 or newer. The package ships a `py.typed` marker, so type
checkers see its annotations without stubs.

## Where to go next

- [Getting started](getting-started.md) — install and local development
- [API reference](api.md) — generated from the source
- [Changelog](changelog.md)
