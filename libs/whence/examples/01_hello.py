"""1 -- Hello, configuration.

The whole idea in one screen and no files: read a value, ask where it came from,
and refuse absence where absence is a bug.

Run it::

    uv run python 01_hello.py    # from a clone of this repository
    python 01_hello.py           # after `pip install whence`, from anywhere

Nothing here touches the filesystem or the environment, so it prints the same
thing on every machine. Each later example adds exactly one real layer.
"""

from whence import Config, MissingKeyError


def main() -> int:
    """Read a mapping, and ask it where its values came from.

    Returns:
        A process exit code.
    """
    # One layer, built in memory. `from_mapping` names it "overrides"; every
    # other example gets its layers from real files instead.
    config = Config.from_mapping({"db": {"host": "localhost", "port": "5432"}})

    print("reading -- the mapping API you already know")
    print(f"  get('db.host')             {config.get('db.host')!r}")
    print(f"  config['db.host']          {config['db.host']!r}")
    print(f"  'db.absent' in config      {'db.absent' in config}")
    # The second positional argument is the default, exactly as on dict.get.
    print(f"  get('db.absent', 'fall')   {config.get('db.absent', 'fall')!r}")
    # Coercion is the named option, because asking for it is the rarer case.
    # Environment variables and .properties files only ever carry strings.
    print(
        f"  get('db.port', type_=int)  {config.get('db.port', type_=int)!r}       <- the file said '5432'"
    )

    print("\nprovenance -- the reason this library exists")
    print(f"  origin('db.host')          {config.origin('db.host')}")
    print(f"  origin('db.absent')        {config.origin('db.absent')}")

    print("\nexplain -- the winner, and everything it shadowed")
    print(config.explain("db.host"))

    print("\nabsence is a choice the caller makes")
    print(f"  get     -> a default       {config.get('db.absent', 'fall')!r}")
    try:
        config.require("db.absent")
    except MissingKeyError as exc:
        print(f"  require -> an exception    MissingKeyError: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
