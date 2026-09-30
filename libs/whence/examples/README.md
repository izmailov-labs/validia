# whence by example

Eight runnable scripts, each adding one idea to the one before it. Read them in
order and you have seen the whole library; run them and you have seen it work.

| | Script | What it adds |
| --- | --- | --- |
| 1 | `01_hello.py` | reading, defaults, coercion, and where a value came from -- no files at all |
| 2 | `02_files.py` | a real TOML file, discovered rather than named, plus interpolation |
| 3 | `03_typed.py` | binding onto frozen dataclasses, and what a typo'd key looks like |
| 4 | `04_layers.py` | the whole precedence stack: cli, env, `.env`, two file roots, defaults |
| 5 | `05_profiles.py` | `demo.prod.toml` overlays, and profile groups |
| 6 | `06_secrets.py` | a secrets directory, `Secret`, and the reveal gate |
| 7 | `07_injection.py` | `@from_config`: a function that declares what it needs |
| 8 | `08_advanced.py` | `RelativePath`, a source you wrote, `with_fallback`, discovery knobs |

## Running them

Every script imports `whence` and the standard library, nothing else, and finds
its data files relative to its own path -- so it behaves the same from any
working directory, under either installer.

```console
# from a clone of this repository
$ uv run python libs/whence/examples/01_hello.py

# or with pip, in any virtual environment
$ python -m venv .venv && source .venv/bin/activate
$ pip install whence
$ python libs/whence/examples/01_hello.py
```

There is no YAML in the examples on purpose: they run on a bare
`pip install whence`, with no extras. `pip install whence[yaml]` adds the one
format that needs a third-party parser.

## The data they read

| Path | The layer it is |
| --- | --- |
| `config/demo.toml` | the base file: interpolation, a `${...:-default}`, an optional `${?...}`, an `${env:...}`, and values that coerce to `bool`, `timedelta` and `list` |
| `config/demo.prod.toml` | a profile overlay, carrying only what differs |
| `config/base/demo.properties` | a **second, lower search root**, in a hand-scanned format that keeps exact `line:column` |
| `config/ca.crt` | the target of a `RelativePath`, resolved against its own config file |
| `demo.env` | a dotenv file, named `demo.env` because this repository gitignores `.env` |
| `secrets/db.password` | a key-per-file secrets directory, which outranks every config file |

`demo` is the application name *and* the file stem: it is what makes
`demo.toml`, `demo.prod.toml`, the `DEMO_` environment prefix, `$DEMO_CONFIG`,
`$DEMO_PROFILES` and `[tool.demo]` all line up. Each of those has its own
override on `Discovery` if you need them decoupled.

## Things to try

Example 4 is the one that reads the real environment and the real command line.

```console
$ cd libs/whence/examples

# the environment outranks every file; __ nests, so this is db.port
$ DEMO_DB__PORT=6543 python 04_layers.py

# the command line outranks even that
$ python 04_layers.py --set db.host=from-the-cli

# an ${env:...} placeholder reads a variable with no prefix at all
$ AWS_REGION=us-east-1 python 04_layers.py

# the profile switch, from outside the process
$ DEMO_PROFILES=prod python 05_profiles.py
```

Two failures are worth causing on purpose.

**A typo'd key.** Add `pool_sze = 1` under `[db]` in `config/demo.toml` and run
example 3 or 4:

```text
BindError: 1 error

  Property: db.pool_sze
     Value: 1
    Origin: .../config/demo.toml
    Reason: no such setting - did you mean 'db.pool_size'?
```

**Two formats in one directory.** `cp config/demo.toml config/demo.json` -- the
JSON will not parse, but discovery fails first, and says what to do about it:

```text
AmbiguousConfigError: .../config holds more than one demo configuration file
(demo.toml, demo.json); remove one, or narrow `formats` to say which wins
```

A preference order would have quietly picked one. Delete the `.json` again when
you are done.

## The CLI, without any of them

The bundled `whence` command reads the same files with no application running.
It uses whence's *default* discovery, which searches the current directory
rather than `config/`, so point it at the files one of two ways:

```console
$ cd libs/whence/examples

# step 2 of discovery: name the file outright
$ DEMO_CONFIG=config/demo.toml whence demo explain db.host

# or just stand in the directory the files are in
$ cd config
$ whence demo explain db.host --profile prod
$ whence demo dump --json
$ whence demo discovery          # every step, including the ones that found nothing
```

## What these examples do not reach

Honest scope, so you know where to look next rather than assuming it is all here.

| Not exercised | Where it lives |
| --- | --- |
| YAML, JSON, XML files | `Discovery(formats=...)` -- the same loader machinery as the two formats used here |
| pydantic models | `@settings` on a `BaseModel` instead of a dataclass; same `load_settings` call |
| `search_parents`, `boundary`, `mode="first"`, `file=` | fields on `Discovery`; example 8 names each and exercises `on_missing` |
| `Binder`, `Problem`, `render_problems` | the binding API underneath `bind`, for reporting problems yourself |
| Kubernetes ConfigMap symlink swaps | covered by the container test suite (`make test-docker`), not by an example |
