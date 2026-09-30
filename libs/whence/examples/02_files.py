"""2 -- Real files, and how they are found.

One TOML file on disk, discovered rather than named, plus a defaults mapping
underneath it. This example adds files and interpolation; it deliberately leaves
the environment, dotenv files and secrets to the next ones.

Run it::

    uv run python 02_files.py
    python 02_files.py           # after `pip install whence`

It reads `config/demo.toml` next to this script, from whatever directory you
run it in: `cwd=HERE` is what makes discovery relative to the example rather
than to your shell.
"""

from pathlib import Path

from whence import Config, Discovery, MissingKeyError

HERE = Path(__file__).parent
# The application name is also the file *stem*: `demo` means `demo.toml`, the
# `DEMO_` environment prefix, `$DEMO_CONFIG` and `[tool.demo]` in pyproject.
APP = "demo"
# Values every source outranks. Present so there is a layer below the file.
DEFAULTS = {"db": {"host": "compiled-in"}, "build": {"commit": "unknown"}}


def discovery() -> Discovery:
    """Look in `config/` only, and ignore the layers later examples add.

    Returns:
        Discovery for this example.
    """
    return Discovery(
        APP,
        path=(Path("config"),),
        dotenv=(),  # example 4
        secrets_dir=None,  # example 6
    )


def main() -> int:
    """Load one file and report what it said and where it lives.

    Returns:
        A process exit code.
    """
    config = Config.load(
        discovery=discovery(),
        cwd=HERE,
        defaults=DEFAULTS,
        environ={},  # example 4 turns the real environment back on
        argv=(),
    )

    print("values -- read straight out of config/demo.toml")
    print(f"  db.host                {config.get('db.host')!r}")
    print(f"  db.port                {config.get('db.port')!r}")
    print(f"  http.hosts             {config.get('http.hosts')!r}")
    print(
        f"  build.commit           {config.get('build.commit')!r}   <- nothing set it; the defaults layer did"
    )

    print("\ninterpolation -- resolved after the merge, never before")
    print(f"  db.url                 {config.get('db.url')!r}")
    print("                         the file says 'postgres://${db.host}:${db.port}/app'")
    print(f"  db.cache               {config.get('db.cache')!r}")
    print("                         ${db.cache_url:-...} -- nothing set it, so the default stood")
    print(f"  http.region            {config.get('http.region')!r}")
    print("                         ${env:AWS_REGION:-eu-west-1} reads a variable with no prefix")
    print(f"  http.proxy             {config.get('http.proxy', '<absent>')!r}")
    print("                         ${?http.proxy_url} drops the key entirely when unset")

    print("\nprovenance")
    print(config.explain("db.host"))
    print("\n  TOML exposes no line numbers, so the origin names the file. YAML,")
    print("  .env and .properties carry an exact line:column -- see example 4.")

    print("\ndiscovery -- every step, including the ones that found nothing")
    print(config.discovery_report())

    print("\nrequiring a key the file does not have")
    try:
        config.require("db.replica_host")
    except MissingKeyError as exc:
        print(f"  MissingKeyError: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
