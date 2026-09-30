"""7 -- Receiving configuration instead of fetching it.

Everything so far *fetched*: hold a `Config`, call `bind`. `@from_config` is the
other direction -- a function declares what it needs in its own signature, and
gets it. This is the one place in whence where configuration is ambient, and it
is ambient only for the length of a `with` block.

Run it::

    uv run python 07_injection.py
    python 07_injection.py       # after `pip install whence`

Wire your own objects explicitly; reach for this at the edges, where a framework
calls your function and there is no call site to thread a `Config` through.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from whence import (
    Config,
    ConfigError,
    Discovery,
    Injected,
    Value,
    current_config,
    from_config,
    settings,
)

HERE = Path(__file__).parent


@settings("db")
@dataclass(frozen=True, slots=True)
class Db:
    """The subtree `Injected` will bind."""

    host: str = "localhost"
    port: int = 5432
    pool_size: int = 5
    debug: bool = False
    url: str = ""
    cache: str = ""


@from_config
def connect(
    *,
    db: Annotated[Db, Injected],
    retries: Annotated[int, Value("http.retries")] = 3,
) -> str:
    """Describe the connection this function would open.

    Neither parameter is passed at the call site. `Injected` binds the
    parameter's own type from its `@settings` subtree; `Value` reads one dotted
    key. Both markers live in `Annotated`, so the annotation stays exact and the
    default stays a default -- `retries` really is 3 when nothing sets the key,
    and mypy still sees an `int`.

    Args:
        db: Bound from the `db` subtree of the ambient configuration.
        retries: Read from `http.retries`, falling back to 3.

    Returns:
        A one-line description.
    """
    return f"{db.host}:{db.port} retries={retries}"


@from_config
async def health(*, db: Annotated[Db, Injected]) -> str:
    """The same thing, on an async function.

    Args:
        db: Bound from the ambient configuration.

    Returns:
        A one-line description.
    """
    await asyncio.sleep(0)
    return f"checking {db.host}"


def main() -> int:
    """Call injected functions inside and outside a configuration scope.

    Returns:
        A process exit code.
    """
    config = Config.load(
        discovery=Discovery(
            "demo", path=(Path("config"),), dotenv=(Path("demo.env"),), secrets_dir=None
        ),
        cwd=HERE,
        environ={},
        argv=(),
    )

    print("inside a scope")
    with current_config(config):
        print(f"  connect()             {connect()}")
        print("                        retries=7 comes from demo.env, so injection")
        print("                        reads the merged result, not the base file")
        # An explicit argument always wins -- this is what a test passes.
        print(f"  connect(retries=1)    {connect(retries=1)}")
        print(f"  await health()        {asyncio.run(health())}")

    print("\noutside it")
    try:
        connect()
    except ConfigError as exc:
        print(f"  connect()             {type(exc).__name__}: {exc}")
    # Nothing is left to fill, so nothing is looked up: a fully-supplied call
    # needs no configuration at all, which is what makes this testable.
    print(f"  connect(db=Db(), retries=1)  {connect(db=Db(host='explicit'), retries=1)}")

    print("\ntwo scopes at once, in the same process")
    tenants = {"eu": "db.eu.internal", "us": "db.us.internal"}
    for tenant, host in tenants.items():
        scoped = Config.from_mapping({"db": {"host": host}}).with_fallback(config)
        with current_config(scoped):
            print(f"  {tenant}: {connect()}")
    print("  the ambient value is a ContextVar, so this is per-task rather than")
    print("  per-process: one tenant's configuration per request, concurrently.")

    print("\nwhat @from_config refuses, and when")
    print("  a marked parameter that is not keyword-only  -> ConfigError at decoration")
    print("  Injected combined with a default             -> ConfigError at decoration")
    print("  two markers on one parameter                 -> ConfigError at decoration")
    print("  (all three at import time, not at a confusing call site later)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
