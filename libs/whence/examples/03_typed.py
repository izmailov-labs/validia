"""3 -- Typed settings: bind a file onto a class.

`get` returns whatever the file happened to contain. A schema turns that into
real Python types once, at startup, and says so loudly when it cannot.

Run it::

    uv run python 03_typed.py
    python 03_typed.py           # after `pip install whence`

The schemas here are frozen dataclasses, which need nothing but the standard
library. A pydantic `BaseModel` works identically -- same decorator, same call.
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from whence import BindError, Config, Discovery, load_settings, settings

HERE = Path(__file__).parent


@settings("db")
@dataclass(frozen=True, slots=True)
class Db:
    """Everything under `[db]`, and nothing else.

    `strict` defaults to True, so a `db.*` key this class does not declare is an
    error rather than a silently ignored typo -- which is the most common
    configuration bug there is.
    """

    host: str = "localhost"
    port: int = 5432
    pool_size: int = 5
    debug: bool = False
    url: str = ""
    cache: str = ""


@settings("http", strict=False)
@dataclass(frozen=True, slots=True)
class Http:
    """Part of `[http]`, deliberately.

    Something else -- a deployment, a sidecar, the `.properties` baseline in
    example 4 -- may set `http.user_agent`, which this class does not declare.
    `strict=False` is how a schema says "I cover part of this subtree on
    purpose", and is the one honest reason to turn the check off.
    """

    timeout: dt.timedelta = dt.timedelta(seconds=30)
    retries: int = 3
    region: str = ""
    hosts: list[str] = field(default_factory=list)


def load(overrides: Mapping[str, object] | None = None) -> Config:
    """Load the example configuration.

    Args:
        overrides: Values layered above the file, so this example can show a
            typo without editing the file on disk.

    Returns:
        The resolved configuration.
    """
    return Config.load(
        discovery=Discovery("demo", path=(Path("config"),), dotenv=(), secrets_dir=None),
        cwd=HERE,
        environ={},
        argv=(),
        overrides=overrides,
    )


def main() -> int:
    """Bind two schemas, then break one on purpose.

    Returns:
        A process exit code.
    """
    config = load()

    # `load_settings(Db, config)` and `config.bind(Db)` are the same call. The
    # first is the typed spelling; `@settings` attaches no methods to your class,
    # because an attribute a decorator adds is invisible to a type checker.
    db = load_settings(Db, config)
    http = load_settings(Http, config)

    print("bound -- every value below arrived as text or a TOML scalar")
    print(f"  db.host       {db.host!r}")
    print(f"  db.port       {db.port!r}          <- int")
    print(f"  db.debug      {db.debug!r}          <- the file says 'no'")
    print(f"  db.url        {db.url!r}")
    print(f"  http.timeout  {http.timeout!r}  <- '30s'")
    print(f"  http.retries  {http.retries!r}")
    print(f"  http.hosts    {http.hosts!r}")
    print(f"  {type(db).__name__} is frozen: {db!r}")

    print("\nstrict -- a key nothing declared is an error, not a shrug")
    print("  layering {'db': {'pool_sze': 1}} on top, as a typo in a file would:")
    try:
        load_settings(Db, load({"db": {"pool_sze": 1}}))
    except BindError as exc:
        print()
        for line in str(exc).splitlines():
            print(f"    {line}")
    print("\n  the same undeclared key under [http], where strict is off:")
    lenient = load_settings(Http, load({"http": {"user_agent": "demo/1.0"}}))
    print(f"    bound anyway, ignoring http.user_agent -> retries={lenient.retries}")

    print("\nerrors arrive in batches, not one per restart")
    try:
        load_settings(Db, load({"db": {"port": "not-a-number", "pool_sze": 1}}))
    except BindError as exc:
        print(f"    {str(exc).splitlines()[0]}")
        print("    (both problems, from one attempt -- fix them in one edit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
