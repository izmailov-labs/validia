"""8 -- The parts you reach for last.

Four things that only make sense once the rest is familiar: a path that resolves
against its own file, a source you wrote yourself, defaults a library ships for
an application to override, and the discovery knobs that decide how strict
startup is.

Run it::

    uv run python 08_advanced.py
    python 08_advanced.py        # after `pip install whence`
"""

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from whence import (
    Config,
    Discovery,
    EnvSource,
    FileSource,
    Layer,
    MissingConfigError,
    Origin,
    RelativePath,
    SourceChain,
    Tracked,
    canonical,
    load_settings,
    settings,
)

HERE = Path(__file__).parent


@settings("tls")
@dataclass(frozen=True, slots=True)
class Tls:
    """A path declared relative to the file that carries it."""

    # default_factory, not a call in the default: an unset key then falls back to
    # an empty path instead of failing to bind at all.
    cert: RelativePath = field(default_factory=RelativePath)


class VaultSource:
    """A stand-in for a secret manager, in the shape every source has.

    A source is anything with a `name` and a `load()` returning one `Layer`. It
    is asked once, synchronously: configuration is read before the application
    runs, so the blocking SDK your secret backend already ships is the right
    shape rather than a workaround.
    """

    def __init__(self, secrets: Mapping[str, str], *, name: str = "vault") -> None:
        """Hold what this pretend vault would return.

        Args:
            secrets: Dotted key to value.
            name: The layer name, used verbatim by `explain`.
        """
        self.name = name
        self._secrets = secrets

    def load(self) -> Layer:
        """Return everything the vault has, as one layer.

        Returns:
            The layer, marked found when the vault held anything.
        """
        # The locator is whatever a human would need to go and look at it.
        origin = Origin(self.name, "vault://demo/database")
        entries = {canonical(key): Tracked(value, origin) for key, value in self._secrets.items()}
        return Layer(self.name, entries, found=bool(entries))


def relative_paths() -> None:
    """Show a path resolved against its own configuration file."""
    config = Config.load(
        discovery=Discovery("demo", path=(Path("config"),), dotenv=(), secrets_dir=None),
        cwd=HERE,
        environ={},
        argv=(),
    )
    tls = load_settings(Tls, config)
    print("RelativePath -- './ca.crt' in config/demo.toml means config/ca.crt")
    print(f"  value      {config.get('tls.cert')!r}")
    print(f"  resolved   {tls.cert}")
    print(f"  exists     {tls.cert.exists()}, from any working directory")
    print("  only expressible because the value remembers which file declared it")


def custom_sources() -> None:
    """Assemble a chain by hand, with a source that is not whence's."""
    chain = SourceChain()
    chain.add_last(EnvSource("DEMO_", environ={"DEMO_DB__HOST": "from-the-env"}))
    chain.add_last(FileSource(HERE / "config" / "demo.toml"))
    # Named insertion, so a caller can place a source without counting indexes.
    chain.add_after("env", VaultSource({"db.password": "s3cret", "db.host": "vault-host"}))

    config = Config(chain.load())
    print("\na chain you assembled yourself")
    print(f"  sources    {list(chain.names())}")
    print(f"  db.host    {config.get('db.host')!r}   <- env, above the vault")
    print(f"  db.password  {config.get('db.password')!r}       <- the raw value, if you ask for it")
    print(
        f"  dump()       {config.dump()['db.password']!r}   <- masked by name, wherever it came from"
    )
    print(config.explain("db.host"))


def library_defaults() -> None:
    """Show a library shipping defaults an application can override."""
    library = Config.from_mapping(
        {"http": {"retries": 3, "timeout": "30s"}}, name="library-defaults"
    )
    application = Config.from_mapping({"http": {"retries": 10}}, name="app")

    merged = application.with_fallback(library)
    print("\nwith_fallback -- fill the gaps, never replace")
    print(f"  http.retries  {merged.get('http.retries')!r}  <- {merged.origin('http.retries')}")
    print(f"  http.timeout  {merged.get('http.timeout')!r}  <- {merged.origin('http.timeout')}")
    print("  HOCON calls this withFallback; figment keeps it separate from merge")
    print("  for the same reason: 'replace' and 'fill in the gaps' differ.")


def strict_startup() -> None:
    """Show the discovery knobs that decide whether startup may proceed."""
    print("\ndiscovery knobs, and what each one is for")
    print("  file=Path(...)        one exact file; wins outright, must exist")
    print("  formats=('toml',)     a stray app.yaml is then never discovered at all")
    print("  mode='first'          stop at the nearest root instead of layering roots")
    print("  search_parents=True   walk upward, for a monorepo")
    print("  on_missing='error'    refuse to start when discovery finds nothing")

    with tempfile.TemporaryDirectory() as empty:
        try:
            Config.load(
                discovery=Discovery("demo", on_missing="error", user_config=False),
                cwd=Path(empty),
                environ={},
                argv=(),
            )
        except MissingConfigError as exc:
            print("\n  on_missing='error' in an empty directory:")
            for line in str(exc).splitlines():
                print(f"    {line.strip()}")


def main() -> int:
    """Run the four sections.

    Returns:
        A process exit code.
    """
    relative_paths()
    custom_sources()
    library_defaults()
    strict_startup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
