"""6 -- Secrets: masked by default, revealed on purpose.

A key-per-file directory -- what Docker and Kubernetes mount at `/run/secrets`
-- outranks every configuration file, because a mounted secret is deployment
truth. The value binds to `Secret`, which refuses to print itself.

Run it::

    uv run python 06_secrets.py
    python 06_secrets.py         # after `pip install whence`

`secrets/db.password` next to this script stands in for the mount. The
inversion is deliberate: pydantic-settings ranks its secrets directory *below*
files, which means a checked-in default can quietly beat a mounted secret.
"""

from dataclasses import dataclass
from pathlib import Path

from whence import (
    Config,
    Discovery,
    Secret,
    SecretError,
    env_name,
    is_sensitive,
    load_settings,
    settings,
    unlock_secrets,
)

HERE = Path(__file__).parent
SECRETS_DIR = HERE / "secrets"  # absolute: not resolved against the cwd


@settings("db")
@dataclass(frozen=True, slots=True)
class Db:
    """The database settings, including one that must not be logged."""

    host: str = "localhost"
    port: int = 5432
    pool_size: int = 5
    debug: bool = False
    url: str = ""
    cache: str = ""
    password: Secret | None = None


def main() -> int:
    """Bind a secret, then show every way it stays out of the logs.

    Returns:
        A process exit code.
    """
    config = Config.load(
        discovery=Discovery("demo", path=(Path("config"),), dotenv=(), secrets_dir=SECRETS_DIR),
        cwd=HERE,
        environ={},
        argv=(),
    )
    db = load_settings(Db, config)

    print("the value is there")
    print(f"  type            {type(db.password).__name__}")
    print(f"  bool            {bool(db.password)}")
    print(f"  came from       {config.origin('db.password')}")

    print("\nbut it does not print")
    print(f"  str()           {db.password}")
    print(f"  repr()          {db.password!r}")
    print(f"  in an f-string  the password is {db.password}")

    print("\nrevealing is a scope, not a method call you can forget")
    if db.password is not None:
        with unlock_secrets():
            print(f"  inside unlock   {db.password.reveal()!r}")
        try:
            db.password.reveal()
        except SecretError as exc:
            print(f"  outside it      {type(exc).__name__}: {exc}")

    print("\nand it does not leak on the way out")
    print(f"  dump()              {config.dump()['db.password']!r}")
    print(f"  dump(reveal=True)   {config.dump(reveal=True)['db.password']!r}")
    print(f"  explain: {config.explain('db.password').splitlines()[0]}")

    print("\nwhat counts as sensitive is a name test, applied everywhere")
    for key in ("db.password", "db.api_token", "db.host"):
        print(f"  {key:<14} {is_sensitive(key)}")

    print("\nthree ways the same secret could arrive")
    print(f"  {SECRETS_DIR.name}/db.password        a mounted directory (this example)")
    print(
        f"  ${env_name(('db', 'password'), 'DEMO_')}_FILE   _FILE indirection: the variable names a path"
    )
    print("  ${file:/run/secrets/db.password}  interpolation, inside any config file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
