# validia — working notes

A universal, async-first evaluation framework for LLM systems. Full spectrum — prompt
evaluation, tool selection, agent evaluation, agent-type comparison — under one set of
primitives.

A **single-package repository** (uv >= 0.12): the repository root *is* the package. It started
as the virtual root of a uv workspace that also held `franca` (the model communication layer)
and `whence` (the configuration layer). Both were extracted into repositories of their own in
September 2026 and are consumed from PyPI like any other dependency:

| Package | Repository | Role here |
| --- | --- | --- |
| `franca` | <https://github.com/izmailov-labs/franca> | Runtime dependency. Transports, connectors, one IR per capability, an adapter per wire dialect, middleware and the model registry. Brings pydantic. |
| `whence` | <https://github.com/izmailov-labs/whence> | Runtime dependency. Typed configuration with full provenance. Zero dependencies of its own. |

Their design history is still here under `docs/research/` (gitignored working notes): the
communication-layer plan and the settings-layer plan describe what became franca and whence.

## Commands

```bash
make install     # uv sync --group dev --group docs + pre-commit install
make lint        # ruff check + ruff format --check
make fmt         # ruff check --fix + ruff format
make typecheck   # mypy (strict, src and tests)
make test        # pytest
make cov         # pytest --cov (fail_under=90)
make encoding    # fail on any read missing an explicit encoding=
make docs        # mkdocs build --strict
make build       # uv build + twine check
make all         # everything CI runs
```

Run one test: `uv run pytest tests/test_smoke.py::test_version_is_exposed`

## Conventions

- **Layout is `src/`.** Tests import the *installed* package (`--import-mode=importlib`), not
  the working tree. Never add an importable package at the repo root.
- **One subpackage per context**, each with its data beside its code: `suites/` (the suite
  model, graders, suite files, example suites in `examples/`, the front-end-agnostic suite
  API), `prompts/` (the guided builder and `default.toml`), `rules/` (the lint engine split
  into `model`, `matching`, `reading`, `catalog`, `layering`, plus `local` for project rule
  files, and the core rule tree in `core/`), `runs/` (model access and the runner) and `cli/`
  (`app` assembling one module per command family on `common`, plus `interview`, `settings`,
  `output` and `_loop`). `suites/` and `prompts/` keep their `__init__` free of imports, because
  `suites.api` and `prompts.building` import each other's packages; `rules/` re-exports its
  public API.
- **Tooling configuration lives once, in `pyproject.toml`.** Ruff, mypy, pytest and coverage are
  configured there alongside the packaging metadata; there is no second config file.
- **mypy is `strict = true` and covers `tests/` too.** New code lands annotated; do not add
  `disallow_untyped_defs = false` or blanket `ignore_missing_imports`. Suppressions must be
  specific: `# type: ignore[code]`, never bare. franca and whence both ship `py.typed`.
- **Async is the default shape.** `pytest-asyncio` runs in auto mode, so `async def test_*` needs
  no decorator. Ruff's `ASYNC` rules are on — they catch blocking calls inside `async def`,
  which is the failure mode that is hardest to spot by reading.
- **Runtime dependencies are inherited by every consumer.** Adding one is a real decision. There
  are exactly two, and both are the sibling packages above:
  - **franca (`>=0.1.1,<1`)** — the call path stays in franca rather than a layer over a vendor
    SDK, because the eval runner needs usage meters, served model, attempts and failure class
    per trial off one object. pydantic (`>=2.12,<3`) arrives transitively; do not re-declare it
    until validia defines models of its own.
  - **whence (`>=1.0,<2`)** — layered settings with provenance, so a bad value reports the file
    and line it came from.

  httpx is **not** a dependency of validia. It sits behind `franca[http]`, and the transport is
  injected, so offline paths (scripted transports, cassettes) never need it. `validia run`'s
  default transport is the one thing that needs real HTTP, and it is the `validia[http]` extra.
  rich is **not** one either: it is the `validia[rich]` extra, for colour and a progress bar
  in `validia run`, and `cli/output.py` is the one place that imports it, lazily, falling
  back to the same lines in plain text. The dev group installs it so tests cover both.
- **Async is asyncio-declared but loop-neutral by construction.** Sleep and time go through an
  injected `Clock` (`franca.core.clock`), HTTP through an injected `Transport`, so no `anyio`:
  a trio user passes their own `TrioClock`. Enforced, not intended — ruff `TID251` bans
  `import asyncio` everywhere in `src/` (tests are exempt) except `src/validia/cli/_loop.py`, the one
  place the command line, an application, drives its event loop. The runner itself
  (`validia.runs.runner`) stays loop-neutral: scheduling trials belongs to the caller, as do a total
  deadline and cancellation (`asyncio.timeout` / `trio.move_on_after`).
- **Configuration goes through `whence`, never `os.environ` directly.** Declare a schema and make
  one call into whence; that is what keeps a bad value reporting the file and line it came from.
- **Every read passes `encoding=` explicitly**, and platform behaviour is **probed, never
  branched on `sys.platform`**. `make encoding` turns a missing `encoding=` into an error,
  because otherwise it only fails on a Windows code page, only for non-ASCII content, and only
  in someone else's CI.
- **Ruff is pinned exactly** (`ruff==0.16.6`). It has no 1.0 and does not follow semver below it,
  so a floating version would silently change the lint gate. The `select` list is explicit for
  the same reason — ruff 0.16 grew its *default* set from 59 to 413 rules.
- **Docstrings are load-bearing**: the docs site generates the API reference from them via
  mkdocstrings, and ruff's `D` rules (google convention) enforce them in `src/`.
- **Public API** is whatever `src/validia/__init__.py` lists in `__all__`. Everything else is
  internal and may change without a major bump.

## Working against an unreleased franca or whence

Both resolve from PyPI, and CI only ever installs from PyPI. A change validia needs in a sibling
lands in that sibling's repository and is released there first; the floor in `dependencies`
then moves up with a changelog entry. To develop against a local checkout in the meantime:

```bash
uv add --editable ../franca      # or ../whence; writes [tool.uv.sources] into pyproject.toml
# ... work ...
git checkout pyproject.toml uv.lock
```

Never commit a `[tool.uv.sources]` path entry: the published wheel would still install the PyPI
release, so the tree and the artifact would disagree.

## Release

One package, so tags are plain: `vX.Y.Z`.

Version is static in `pyproject.toml`; `__version__` reads it back at runtime via
`importlib.metadata`. To release: bump the version, write the `CHANGELOG.md` entry, tag `vX.Y.Z`,
push the tag. `release.yml` verifies the tag against `pyproject.toml`, builds, and publishes via
PyPI Trusted Publishing (OIDC — there is no API token in this repo).

Two constraints worth remembering: the **trusted publisher must be configured on PyPI before the
first tag** (owner `izmailov-labs`, repository `validia`, workflow `release.yml`, environment
`pypi`), and **PyPI rejects new files added to a release older than 14 days**, so everything for
a version ships in one run.

## Docs site

<https://validia.dev> is GitHub Pages serving the `gh-pages` branch. `docs.yml` runs
`mkdocs gh-deploy --force` on every push to `main`, which rewrites that branch from scratch, so
the custom domain lives in `docs/CNAME` (copied into each build) — delete it and the next deploy
drops the domain. DNS is at GoDaddy: apex A/AAAA records to GitHub Pages, `www` CNAME to
`izmailov-labs.github.io`. `.dev` is HSTS-preloaded, so the site is unreachable over plain HTTP
until Pages has issued its certificate.

## Open decisions

- **Docs generator.** MkDocs core has not released since 1.6.1 and Material has
  pinned itself to `mkdocs<2`; the forward path is Zensical, which reads this same
  `mkdocs.yml` but is still 0.0.x. Material is the right choice today; revisit
  when Zensical stabilises.
