# validia.dev

A [uv](https://docs.astral.sh/uv/) workspace. Every distributable package lives under
[`libs/`](libs/) with its own version, changelog and release tag; this root is virtual and is
never published.

## Packages

| Package | Path | What it is |
| --- | --- | --- |
| [`franca`](libs/franca) | `libs/franca` | One request shape for every LLM wire dialect: transports, adapters, streaming, and a model registry. |
| [`validia`](libs/validia) | `libs/validia` | A universal, async-first evaluation framework for LLM systems: prompts, tools, and agents. |

## Development

Everything runs from this directory and covers every member at once.

```bash
make install     # sync dev + docs groups for the workspace, install pre-commit hooks
make all         # lint, typecheck, coverage, docs, build
```

| Command | What it does |
| --- | --- |
| `make lint` | `ruff check` + `ruff format --check` |
| `make typecheck` | `mypy` in strict mode |
| `make test` | `pytest` across every member |
| `make cov` | Tests with coverage (fails under 90%) |
| `make docs` | Build the docs site with `--strict` |
| `make build` | Build every member into `./dist` and validate the metadata |
| `make build-one PKG=validia` | Build a single member |

One lockfile, one virtual environment and one lint, type and coverage gate serve the whole
workspace. Lint, type, test and coverage configuration lives only in the root
`pyproject.toml`; a member's `pyproject.toml` carries its packaging metadata and nothing else.

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

- **One member per PR where possible.** Members version and release independently, so a change
  scoped to `libs/whence` is easier to review, revert and ship than one straddling three.
- **A changelog entry** under `## [Unreleased]` in that member's `CHANGELOG.md`, using the
  Keep a Changelog sections already in the file. Versions are bumped at release time, not here.
- **Annotated code, tests included.** mypy runs `strict = true` over `src/` and `tests/` alike,
  and suppressions are specific: `# type: ignore[code]`, never bare.
- **Docstrings on public API.** The docs site generates its reference from them, and ruff's `D`
  rules (google convention) enforce them in `src/`.
- **Test module basenames unique across members.** mypy maps `libs/x/tests/test_foo.py` to the
  module `test_foo`, so two members with the same filename collide even though pytest tolerates
  it.
- **No new tooling tables.** Ruff, mypy, pytest and coverage are configured once in the root
  `pyproject.toml`; a `[tool.ruff]` or `[tool.mypy]` table in a member forks the gate.
- **A new runtime dependency is a decision, not a detail** — every consumer inherits it. Open an
  issue before the PR. Dev tooling goes in `[dependency-groups]`, which is never published, and
  an optional feature goes behind an extra.
- **`uv.lock` committed** whenever a dependency changes; the `uv-lock` hook fails on drift.

Two rules exist because breaking them fails late and somewhere else: **probe, never branch on
`sys.platform`**, and **pass `encoding=` explicitly on every read**. If a change touches mounts,
musl or a non-UTF-8 locale, `make test-docker` runs the Linux scenarios CI cannot reproduce.

Anything large enough to have a design is worth an issue before the PR; small fixes can go
straight to one.

## Releasing

Tags are package-scoped, because members release on their own cadence:

```
<package>-v<version>        e.g. validia-v0.1.0
```

The release workflow verifies the tag against that member's `pyproject.toml`, builds only that
member, and publishes it through PyPI Trusted Publishing. Each package needs its own PyPI
project and its own trusted publisher configured before its first tag.

## Documentation

<https://validia.dev>

## License

MIT — see [LICENSE](LICENSE).
