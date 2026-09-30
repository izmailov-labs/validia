# validia.dev — working notes

A **uv workspace** (>= 0.12). The repository root is virtual: it has no `[project]`
table and is never published. Every distributable package lives under `libs/<name>/`
with its own `pyproject.toml`, version, changelog and release tag.

| Package | Path | What it is |
| --- | --- | --- |
| `franca` | `libs/franca` | The model communication layer: transports, connectors, one IR per capability, an adapter per wire dialect, middleware and a registry. Standalone-useful; `validia` will depend on it. |
| `whence` | `libs/whence` | Typed configuration that remembers where it came from: layered loading from env, `.env`, TOML, JSON, YAML, `.properties` and XML, with full provenance. Zero runtime dependencies. |
| `validia` | `libs/validia` | A universal, async-first evaluation framework for LLM systems. Full spectrum — prompt evaluation, tool selection, agent evaluation, agent-type comparison — under one set of primitives. |

## Commands

```bash
make install     # uv sync --group dev --group docs + pre-commit install
make lint        # ruff check + ruff format --check
make fmt         # ruff check --fix + ruff format
make typecheck   # mypy (strict)
make test        # pytest
make cov         # pytest --cov (fail_under=90)
make encoding    # fail on any read missing an explicit encoding=
make test-docker # the Linux scenarios CI cannot reproduce (see below)
make docs        # mkdocs build --strict
make build       # uv build --all-packages + twine check
make build-one PKG=validia   # build a single member
make all         # everything CI runs
```

Every command runs from the repository root and covers all members at once; there is
one virtual environment and one lockfile for the workspace.

Run one test: `uv run pytest libs/validia/tests/test_smoke.py::test_version_is_exposed`
Work on one member: `uv run --package validia <cmd>`
One container scenario: `make test-docker-one S=musl`

## Cross-platform testing

Three layers, because no single one is enough and each covers what the others
cannot.

| Layer | Covers | Cannot cover |
| --- | --- | --- |
| **Seams** (`_platform.flavour`, `WindowsEnviron` in whence's tests) | Windows and macOS *semantics* from any runner: path flavour, environment case-folding, config-directory conventions | Real filesystem behaviour |
| **CI matrix** (`.github/workflows/ci.yml`) | The three real operating systems, at both ends of the Python range | Container mounts, musl, non-UTF-8 locales |
| **Docker** (`make test-docker`) | musl, a POSIX locale, a read-only root, an unprivileged user, a real `/run/secrets` tmpfs, a live Kubernetes ConfigMap symlink swap | macOS and Windows -- neither runs in a Linux container |

Tests needing a real mount carry the `container` marker and are excluded from the
default run; `make test-docker` supplies `WHENCE_CONTAINER=1` and the mounts.

Two rules that keep the matrix honest. **Probe, never branch on `sys.platform`**
— case-insensitivity is a property of a directory, not a platform, and a name
that can be created is not always a name that round-trips. And **every read
passes `encoding=` explicitly**: `make encoding` turns a missing one into an
error, because otherwise it only fails on a Windows code page, only for
non-ASCII content, and only in someone else's CI.

## Conventions

- **Layout is `libs/<package>/src/`.** Never add an importable package at the repo
  root or at a member's root — the point of `src/` is that tests import the
  *installed* package (`--import-mode=importlib`), not the working tree.
- **Tooling configuration lives once, in the root `pyproject.toml`.** Ruff, mypy,
  pytest and coverage are configured there for the whole workspace; a member's
  `pyproject.toml` carries packaging metadata and nothing else. Adding a
  `[tool.ruff]` or `[tool.mypy]` table to a member forks the gate — don't.
  A new member needs its `src/` path added to `[tool.ruff] src`, `[tool.mypy] files`
  and `[tool.coverage.run] source`; `testpaths = ["libs"]` picks its tests up
  automatically.
- **Test module basenames stay unique across members.** mypy maps
  `libs/x/tests/test_foo.py` to the module `test_foo`, so two members with the same
  test filename collide under strict mode even though pytest tolerates it.
- **Cross-member dependencies go through `[tool.uv.sources]`** with
  `{ workspace = true }`, so development resolves to the local path while consumers
  installing from PyPI get the published release. That table is dev metadata and
  does not reach the built wheel.
- **mypy is `strict = true` and covers `tests/` too.** New code lands annotated;
  do not add `disallow_untyped_defs = false` or blanket `ignore_missing_imports`.
  Suppressions must be specific: `# type: ignore[code]`, never bare.
- **Async is the default shape.** `pytest-asyncio` runs in auto mode, so
  `async def test_*` needs no decorator. Ruff's `ASYNC` rules are on — they catch
  blocking calls inside `async def`, which is the failure mode that is hardest to
  spot by reading.
- **Runtime dependencies are inherited by every consumer.** `dependencies` in
  `pyproject.toml` is empty on purpose; adding one is a real decision. Dev tooling
  goes in `[dependency-groups]`, which is not published. The one exception:
  **pydantic (`>=2.12,<3`) is franca's single runtime dependency** — the stdlib route
  is `Any`-heavy code owned forever, and dataclasses validate nothing on the outbound
  request, so a bad request would carry no field path (see §11 of the communication
  layer plan). `validia` stays at zero, and adding any other is still a real decision.
  httpx is an optional extra (`franca[http]`), imported lazily inside
  `HttpxTransport.__init__` so `pip install franca` works without it.
- **Async is asyncio-declared but loop-neutral by construction.** Only four operations
  touch the loop — sleep, monotonic/wall time, HTTP I/O, async-generator close — and
  each goes through an injected `Clock` or `Transport`, or an explicit `aclose()`. So
  no `anyio`: a trio user passes their own `TrioClock` plus `HttpxTransport`. This is
  enforced, not merely intended — ruff `TID251` bans `import asyncio` everywhere in
  `src/` except `franca/core/clock.py`. Deadlines are `clock.monotonic()` arithmetic;
  a total deadline and cancellation belong to the caller (`asyncio.timeout` /
  `trio.move_on_after`), so core has no `asyncio.timeout`, task groups or locks.
- **Ruff is pinned exactly** (`ruff==0.16.6`). It has no 1.0 and does not follow
  semver below it, so a floating version would silently change the lint gate. The
  `select` list is explicit for the same reason — ruff 0.16 grew its *default* set
  from 59 to 413 rules.
- **Docstrings are load-bearing**: the docs site generates the API reference from
  them via mkdocstrings, and ruff's `D` rules (google convention) enforce them in
  `src/`.
- **Configuration goes through `whence`, never `os.environ` directly.** A member
  that needs settings declares a schema and calls `whence`; that is what keeps a
  bad value reporting the file and line it came from. `whence` itself has no
  runtime dependencies and, unlike franca, no async at all: configuration is
  read once before the app runs, so `Config.load` is an ordinary synchronous
  call and ruff's `TID251` ban on `import asyncio` has no exemption in whence.
- **Public API** is whatever `src/validia/__init__.py` lists in `__all__`.
  Everything else is internal and may change without a major bump.

## Diagrams

`diagrams/franca-core.*` is the whole communication layer in one picture; `diagrams/panels/`
holds higher-detail per-area sources. The `.mmd` files are the source of truth, never the
PNG: edit those and re-render with the `/diagram` skill.

Three label rules, all learned by breaking them. Labels must stay **ASCII**, because the renderer
ships source into the page through `atob()`. Labels must contain **no bare `<` or `>`**, including
`->` and `<=`, because mermaid HTML-escapes them and you get `&gt;` in the output. And labels must
contain **no `<br/>`**: it works in SVG and PNG, but the excalidraw converter emits it as literal
`<br>` text and then word-wraps mid-word, so line breaks silently corrupt the editable scene. Use
single-line labels and let the renderer wrap.

Contrary to the `/diagram` skill's own documentation, the bundled converter **does** turn a
`classDiagram` into an editable `.excalidraw`; this was verified against the bundle, not assumed.

## Release

Each member releases on its own cadence, so **tags are package-scoped**:
`<package>-vX.Y.Z`, e.g. `validia-v0.1.0`.

Version is static in the member's `pyproject.toml`; `__version__` reads it back at
runtime via `importlib.metadata`. To release: bump the version in
`libs/<pkg>/pyproject.toml`, write the `libs/<pkg>/CHANGELOG.md` entry, tag
`<pkg>-vX.Y.Z`, push the tag. `release.yml` parses the package name out of the tag,
verifies the version against that member's `pyproject.toml`, builds **only** that
member, and publishes via PyPI Trusted Publishing (OIDC — there is no API token in
this repo).

Three constraints worth remembering: each package needs **its own PyPI project and
its own trusted publisher**, configured before its first tag; **PyPI rejects new
files added to a release older than 14 days**, so everything for a version ships in
one run; and a sibling package is never published as a side effect of someone else's
release.

## Open decisions

- **Docs generator.** MkDocs core has not released since 1.6.1 and Material has
  pinned itself to `mkdocs<2`; the forward path is Zensical, which reads this same
  `mkdocs.yml` but is still 0.0.x. Material is the right choice today; revisit
  when Zensical stabilises.
