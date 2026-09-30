# whence

**Typed configuration that remembers where it came from.**

Every configuration library can tell you a value. `whence` can tell you *why* it
has that value — which file, which line, which profile, and what it overrode.

```console
$ whence myapp explain db.host --profile prod
db.host = 'db.internal'
  <- config/app.prod.yaml:4:9 [profile=prod]
  shadowed:
      overrides                      - not set
      env                            - not set
      dotenv:.env                    - not found
      secrets-dir                    - not found
      config/app.yaml:2:9            = 'localhost'
      defaults                       = 'localhost'
```

Spring has this (`Origin`, `/actuator/env`), HOCON has it (`ConfigOrigin`), Rust's
figment has it (`Metadata`). Python has not — so that is what `whence` is for. It
is a **complement to pydantic**, not a replacement: point it at a pydantic model
or a frozen dataclass and it handles the layering, the provenance and the
diagnostics.

## Install

```console
pip install whence          # env, .env, TOML, JSON, .properties, XML
pip install whence[yaml]    # + YAML
```

The core has **no runtime dependencies**. Every format but YAML is parsed on the
standard library.

## Quick start

```python
from dataclasses import dataclass
from whence import Config, Secret, load_settings, settings


@settings(prefix="db")
@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432
    password: Secret | None = None


cfg = Config.load(app="myapp", profiles=["prod"])
db = load_settings(Db, cfg)
```

Loading is synchronous. Configuration is read once, before the application
runs, so there is no event loop for an `await` to yield to; a `Source` that
reaches the network blocks the startup it is already part of.

## Receiving configuration

The code above *fetches*: it holds a `Config` and binds. `@from_config` is the
other direction, for the edges where a framework calls your function and there
is no call site to thread a `Config` through:

```python
from typing import Annotated
from whence import Injected, Value, current_config, from_config


@from_config
def connect(
    *,
    db: Annotated[Db, Injected],
    retries: Annotated[int, Value("http.retries")] = 3,
) -> Conn: ...


with current_config(cfg):
    connect()  # db bound from the `db` subtree, retries from the key
    connect(retries=1)  # an explicit argument always wins
```

Both markers live in `Annotated`, so the annotation stays exact and the default
stays a real default — `retries` is 3 when nothing sets the key, and a call that
supplies every marked argument needs no ambient configuration at all. The
ambient value is a `ContextVar`, so it is per-task rather than per-process: one
tenant's configuration per request, concurrently, and nothing leaking into a
worker thread that should have been passed a `Config` explicitly.

A marked parameter must be keyword-only, which is checked at decoration —
filling happens through `kwargs`, so an argument passed positionally would be
filled twice.

## Precedence

Seven named layers, highest first. `whence <app> explain <key>` prints them.

| Layer | Source |
| --- | --- |
| `overrides` | values passed to `Config.load(overrides=...)` |
| `cli` | `--set a.b=c` in `sys.argv` |
| `env` | `MYAPP_DB__HOST`, plus `_FILE` indirection |
| `dotenv` | `.env.{profile}`, then `.env` |
| `secrets-dir` | `/run/secrets`, one file per key |
| `files` | `app.{profile}.{ext}`, then `app.{ext}` |
| `defaults` | schema defaults |

The secrets directory sits **above** configuration files, unlike
pydantic-settings: a secret an operator deliberately mounted is deployment
truth, not a fallback.

## Discovery

Nothing about *where* configuration lives is hard-coded. Five steps, each
reported by `whence <app> discovery` even when it finds nothing:

1. an explicit `file=` — missing is an error
2. `$MYAPP_CONFIG`
3. each root in `path`
4. the per-user configuration directory
5. `pyproject.toml` → `[tool.myapp]`

```python
Discovery(
    app="myapp",
    path=(Path("config"), Path("/etc/myapp")),
    formats=("toml", "yaml"),
    prefix="MYAPP_",
    search_parents=True,
    mode="layer",
)
```

Two files matching in the *same* root raise `AmbiguousConfigError` rather than
resolving by preference — the case Spring left unspecified for years. Across
different roots there is no ambiguity: the earlier root wins, and both appear in
`explain`.

## Profiles

Profile files overlay the base file, profile groups expand, and the invariant is
that **profiles gate documents; they never reorder sources**. A profiled key in
a low-precedence source can never outrank an unprofiled key in a higher one —
the rule that eliminates the "why did dev configuration win in production?"
class of incident.

## Interpolation

`${a.b}`, `${a.b:-default}` (POSIX `:-`, so `${url:-redis://h:6379}` is
unambiguous), `${?a.b}` (the key disappears when undefined), `${env:VAR}`,
`${file:/run/secrets/pw}`, and `\${literal}`. Resolved lazily after the merge,
so a base file can reference a key a higher source supplies. There is no global
"ignore unresolvable" switch: that is the setting that turns a startup failure
into a literal `${db.password}` reaching a database driver.

## Errors

Every problem in one run, each carrying its origin:

```text
whence.BindError: 2 errors

  Property: db.port
     Value: 'eighty'
    Origin: config/app.prod.yaml:4:9 [profile=prod]
    Reason: could not convert to int
  Shadowed: config/app.yaml:5:9 = 5432

  Property: db.max_retires
     Value: 3
    Origin: config/app.yaml:8:3
    Reason: no such setting - did you mean 'db.max_retries'?

Action: correct the configuration, or run `whence explain <key>`.
```

No message ever contains the value it rejected — a rejected value may be a
secret, and an exception is the most widely logged object in a program.

## Reloading

There isn't any. To change configuration, redeploy.

`Config` is immutable and loaded once. A library that also owns a poll loop owns
a thread or an event loop, and the generational swap it needs is where every
prior art went wrong: .NET's `ChangeToken.OnChange` has fired **twice per save**
since 2017 and ships a hash-and-backoff workaround in its own documentation, Go
viper's `WatchConfig` carries a documented data race, and koanf's `Watch()` is
not safe against concurrent reads. Each of those is a property of mutating an
object other code is holding — so whence never holds one.

A fresh `Config.load(...)` does see the current state of the world, including
through a **Kubernetes ConfigMap's `..data` symlink**. kubelet republishes by
pointing that symlink at a new timestamped directory; an inotify watch on the
config *file* is bound to an inode that has been unlinked and goes permanently
deaf, which is the single most common way this is got wrong — Python's watchdog
fails it silently because it hardcodes `IN_DONT_FOLLOW`. Reading resolves the
symlink afresh every time. A container test builds kubelet's exact layout with
real symlinks and asserts the swap is seen (`make test-docker`).

If a running process must pick up a change, call `Config.load(...)` again on a
schedule you own and swap your own pointer. That is a handful of lines, it lives
where your concurrency model already is, and whence stays out of it.

## Cross-platform

Configuration directories follow each platform's own convention: XDG on Linux,
`~/Library/Application Support` on macOS (plus XDG when explicitly set),
`%LOCALAPPDATA%` then `%APPDATA%` on Windows. Windows folds environment variable
names to upper case before any library code runs, and whence's naming scheme is
bijective under that fold, so the same code and the same tests behave
identically everywhere. Files are read as UTF-8 with a BOM tolerated, CRLF
parses, and case-insensitive filesystems do not produce phantom ambiguities.

## API reference

::: whence
