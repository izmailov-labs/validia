# whence

**Typed configuration that remembers where it came from.**

Every configuration library can tell you a value. `whence` can tell you *why*
it has that value — which file, which line, which profile, and what it
overrode.

```console
$ whence explain db.host --profile prod
db.host = "db.internal"
  <- config/app.prod.yaml:4:9 [profile=prod]
  shadowed:
      overrides                     - not set
      MYAPP_DB__HOST                - not set
      .env                          - not set
      config/app.yaml:2:9           = "localhost"
      <defaults>                    = "localhost"
```

Spring has this (`Origin`, `/actuator/env`), HOCON has it (`ConfigOrigin`), Rust's
`figment` has it (`Metadata`). Python has not — pydantic-settings' debug output
is top-level-only and unredacted, dynaconf has history but no line numbers, and
everything else discards provenance at the first merge.

`whence` is a **complement to pydantic, not a replacement**. Point it at a
pydantic model or a frozen dataclass; it does the layering, the provenance and
the diagnostics.

## Install

```console
pip install whence          # env, .env, TOML, JSON, .properties, XML
pip install whence[yaml]    # + YAML
```

The core has **no runtime dependencies**. That is a design constraint, not an
accident: every format but YAML is parsed on the standard library.

## Use

```python
from dataclasses import dataclass
from whence import Config, Secret, settings


@settings(prefix="db")
@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432
    password: Secret | None = None


cfg = Config.load(app="myapp", profiles=["prod"])
db = cfg.bind(Db)

cfg.explain("db.host")  # the winner, and everything it shadowed
```

Eight runnable scripts, simple to advanced, live in [`examples/`](examples/) —
reading and provenance with no files at all, then files, schemas, the whole
precedence stack, profiles, secrets, `@from_config`, and the parts you reach for
last. They need nothing but `pip install whence`.

Loading is synchronous, and deliberately so. Configuration is read once,
before the application runs — there is no event loop at that point, so an
`await` would have nothing to yield to. A `Source` that reaches the network
blocks the startup it is already part of, which is exactly what starting up
means.

## What it does

| | |
|---|---|
| **Formats** | env vars, `.env`, TOML, JSON, YAML, Java `.properties`, XML |
| **Precedence** | seven named layers, `overrides > cli > env > .env > secrets-dir > files > defaults` |
| **Discovery** | explicit path → `$MYAPP_CONFIG` → project roots → per-user config dir → `pyproject.toml`, all configurable |
| **Profiles** | `app.prod.yaml` overlays, profile groups, and profiles never reorder sources |
| **Interpolation** | `${a.b}`, `${a.b:-default}`, `${?a.b}`, `${env:VAR}`, `${file:/run/secrets/x}` |
| **Secrets** | `Secret`, `_FILE` indirection, a sanitizer on every dump, reads gated by `unlock_secrets()` |
| **Binding** | frozen dataclasses on the stdlib; pydantic models when pydantic is installed |
| **Diagnostics** | batched errors carrying origin, and did-you-mean for unknown keys |

Exact `line:column` comes from YAML, `.env` and `.properties`. `tomllib`, `json`
and `ElementTree` expose no positions, so TOML, JSON and XML get file-level
origins — stated here rather than implied away.

## Reloading

There isn't any. To change configuration, redeploy.

`Config` is immutable and loaded once. A library that also owns a poll loop owns
a thread or an event loop, and the generational swap it needs is where every
prior art went wrong: .NET's `ChangeToken.OnChange` has fired **twice per save**
since 2017, Go viper's `WatchConfig` carries a documented data race, and koanf's
`Watch()` is not safe against concurrent reads. Each of those is a property of
changing an object other code is holding — so whence does not hold one.

What whence does guarantee is that a fresh `Config.load(...)` sees the current
state of the world, including through a **Kubernetes ConfigMap's `..data`
symlink**. kubelet republishes by pointing that symlink at a new directory, and
an inotify watch on the config *file* is bound to the old inode and goes
permanently deaf; re-reading resolves the symlink afresh. That is covered by a
test against real symlinks on a real Linux filesystem (`make test-docker`).

If you need a running process to pick up a change, call `Config.load(...)` again
on a schedule you own and swap your own pointer. It is a handful of lines, it
lives where your concurrency model already is, and whence stays out of it.

## Cross-platform

Config directories follow each platform's own convention: XDG on Linux,
`~/Library/Application Support` on macOS (plus XDG when you set it explicitly),
`%APPDATA%` and `%LOCALAPPDATA%` on Windows. Case-insensitive filesystems do not
produce phantom ambiguities, CRLF files parse, and every file is read as UTF-8
regardless of the platform's default encoding.

## License

MIT
