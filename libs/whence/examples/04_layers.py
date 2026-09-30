"""4 -- Layers: what beats what, and why you can prove it.

Five sources at once, all real: the command line, the environment, a dotenv
file, two file roots in two formats, and a defaults mapping. The precedence
order is fixed and named, and `explain` shows the whole stack for one key --
the winner on top, everything it shadowed underneath.

Run it, then run it again with something set::

    uv run python 04_layers.py
    DEMO_DB__PORT=6543 python 04_layers.py
    python 04_layers.py --set db.host=from-the-cli

`__` nests, so `DEMO_DB__PORT` is `db.port`. Nothing is mocked here: whence
reads `os.environ` and `sys.argv` itself.
"""

from pathlib import Path

from whence import Config, Discovery, env_name

HERE = Path(__file__).parent
# Two roots, nearest first. config/base holds a .properties baseline, a format
# whence scans by hand -- so its origins carry an exact line and column, where
# tomllib exposes no positions at all.
CONFIG_DIRS = (Path("config"), Path("config/base"))
DOTENV = (Path("demo.env"),)  # `.env` by default; renamed here to survive git
DEFAULTS = {"db": {"host": "compiled-in"}, "build": {"commit": "unknown"}}


def main() -> int:
    """Load every layer at once and explain one key through all of them.

    Returns:
        A process exit code.
    """
    config = Config.load(
        discovery=Discovery("demo", path=CONFIG_DIRS, dotenv=DOTENV, secrets_dir=None),
        cwd=HERE,
        defaults=DEFAULTS,
        # environ and argv left at their defaults: the real ones.
    )

    print("precedence -- highest first, and every layer is consulted")
    print("  overrides > cli > env > .env > secrets-dir > files > defaults")

    print("\ndb.host, through all of it")
    print(config.explain("db.host"))

    print("\nposition -- two of these formats know their own line and column")
    for key in ("http.user_agent", "http.retries"):
        print(f"  {key:<18} = {config.get(key)!r}")
        print(f"  {'':<18}   <- {config.origin(key)}")

    print("  http.retries is text: .env and .properties carry only strings, and")
    print("  example 3's schema is what turns it into an int.")

    print("\nnaming -- the variable that would set each key")
    for key in ("db.host", "db.port", "http.retries"):
        print(f"  {key:<18} {env_name(tuple(key.split('.')), 'DEMO_')}")

    print("\nnow set")
    print(
        f"  db.port      = {config.get('db.port')!r}    try: DEMO_DB__PORT=6543 python 04_layers.py"
    )
    print(
        f"  db.pool_size = {config.get('db.pool_size')!r}       try: python 04_layers.py --set db.pool_size=99"
    )
    print(
        f"  http.region  = {config.get('http.region')!r}   try: AWS_REGION=us-east-1 python 04_layers.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
