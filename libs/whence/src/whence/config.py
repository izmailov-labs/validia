"""The immutable, resolved configuration object, and how one is built."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import binding as _binding
from ._platform import Platform
from .binding.coerce import coerce
from .chain import SourceChain
from .discovery import Discovery, DiscoveryPlan, PyProjectSource
from .errors import MissingKeyError
from .interpolate import interpolate
from .keys import KeyPath, canonical, join
from .origin import Origin, Tracked
from .profiles import active_profiles
from .secret import sanitize
from .sources.argv import ArgvSource
from .sources.dotenv import DotEnvSource
from .sources.env import EnvSource
from .sources.mapping import MappingSource
from .sources.secrets import SecretsDirSource
from .tree import Layer, Resolved, resolve, unflatten

__all__ = ["Config"]

_MISSING = object()


class Config:
    """Resolved configuration, plus the provenance of everything in it.

    A ``Config`` is immutable. Reloading builds a new one and swaps it, which is
    the only design that cannot expose a half-updated object: .NET's in-place
    ``IOptionsMonitor`` fires twice per save and Go viper's watcher carries a
    documented data race, and both problems are properties of mutating in place.
    """

    def __init__(
        self,
        resolved: Resolved,
        *,
        plan: DiscoveryPlan | None = None,
        profiles: Sequence[str] = (),
        discovery: Discovery | None = None,
    ) -> None:
        """Wrap an already-resolved merge.

        Args:
            resolved: The merged values and shadow records.
            plan: The discovery plan that produced the file sources.
            profiles: The active profiles, in increasing precedence.
            discovery: The discovery settings used.
        """
        self._resolved = resolved
        self.plan = plan
        self.profiles = tuple(profiles)
        self.discovery = discovery

    # ------------------------------------------------------------------ build

    @classmethod
    def load(
        cls,
        app: str | None = None,
        *,
        discovery: Discovery | None = None,
        profiles: Sequence[str] | None = None,
        groups: Mapping[str, Sequence[str]] | None = None,
        overrides: Mapping[str, Any] | None = None,
        argv: Sequence[str] | None = None,
        defaults: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        aliases: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        platform: Platform | None = None,
        expand: bool = True,
    ) -> "Config":
        """Discover, load and merge every source.

        Args:
            app: The application name; shorthand for ``Discovery(app=...)``.
            discovery: Full discovery settings, if ``app`` is not enough.
            profiles: Active profiles; falls back to the profiles variable.
            groups: Profile group definitions.
            overrides: Values that outrank every source.
            argv: Command-line arguments to scan for ``--set key=value``. Pass
                ``()`` to disable, or leave as ``None`` to read ``sys.argv``.
            defaults: Values every source outranks.
            environ: The environment to read; defaults to ``os.environ``.
            aliases: An explicit map from variable name to dotted key, for the
                cases convention does not cover. An explicit, greppable table
                beats implicit name mangling.
            cwd: The directory discovery starts from.
            platform: Override the platform, for cross-platform tests.
            expand: Whether to expand ``${...}`` placeholders.

        Returns:
            The resolved configuration.

        Raises:
            ConfigError: If discovery or any source fails.
        """
        settings = discovery or Discovery(app=app or "app")
        env = os.environ if environ is None else environ
        active = active_profiles(profiles, environ=env, var=settings.profiles_env, groups=groups)
        plan = settings.plan(profiles=active, environ=env, cwd=cwd, platform=platform)
        base_dir = Path.cwd() if cwd is None else cwd

        chain = SourceChain()
        if overrides:
            chain.add_last(MappingSource(overrides, name="overrides"))
        if argv is None or argv:
            chain.add_last(ArgvSource(argv))
        chain.add_last(
            EnvSource(
                settings.env_prefix,
                environ=env,
                aliases=aliases,
                # These say where to look, not what to load.
                exclude=(settings.config_var, settings.profiles_env),
            )
        )
        for path in settings.dotenv:
            base = path if path.is_absolute() else base_dir / path
            for profile in reversed(active):
                chain.add_last(
                    DotEnvSource(
                        base.with_name(f"{base.name}.{profile}"),
                        settings.env_prefix,
                        name=f"dotenv:{base.name}.{profile}",
                    )
                )
            chain.add_last(DotEnvSource(base, settings.env_prefix, name=f"dotenv:{base.name}"))
        if settings.secrets_dir is not None:
            chain.add_last(SecretsDirSource(settings.secrets_dir))
        for source in settings.file_sources(plan):
            chain.add_last(source)
        if settings.table:
            chain.add_last(PyProjectSource(base_dir / "pyproject.toml", settings.table))
        if defaults:
            chain.add_last(MappingSource(defaults, name="defaults", locator="<defaults>"))

        merged = chain.load()
        if expand:
            merged = replace(merged, values=interpolate(merged.values, environ=env))
        return cls(merged, plan=plan, profiles=active, discovery=settings)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, name: str = "overrides") -> "Config":
        """Build a configuration from a single mapping, for tests and defaults.

        Args:
            data: Nested or flat configuration values.
            name: The layer name.

        Returns:
            The resolved configuration.
        """
        source = MappingSource(data, name=name)
        return cls(resolve([Layer(name, source._entries, found=bool(data))]))

    # ------------------------------------------------------------------- read

    @property
    def values(self) -> Mapping[KeyPath, Tracked]:
        """The winning tracked value for every key.

        Returns:
            Flat key paths to tracked values.
        """
        return self._resolved.values

    def _tracked(self, key: str) -> Tracked | None:
        """Look a key up by its canonical path."""
        return self._resolved.values.get(canonical(key))

    def __contains__(self, key: str) -> bool:
        """Report whether a key has a value."""
        return canonical(key) in self._resolved.values

    def __getitem__(self, key: str) -> Any:
        """Return a value, raising if it is absent."""
        return self.require(key)

    def get(self, key: str, default: Any = None, *, type_: Any = None) -> Any:
        """Return a value, or a default.

        The second positional argument is the default, as it is on ``dict`` and
        ``os.environ``: ``cfg.get("db.port", 5432)`` means what it looks like.
        Coercion is the named option, because asking for it is the rarer case.

        Args:
            key: A dotted key.
            default: Returned when the key is absent. Never coerced -- it is
                already a Python value.
            type_: Coerce to this type when given.

        Returns:
            The value, coerced if asked.
        """
        tracked = self._tracked(key)
        if tracked is None:
            return default
        if type_ is None:
            return tracked.value
        return coerce(tracked.value, type_)

    def require(self, key: str, *, type_: Any = None) -> Any:
        """Return a value, raising a helpful error if it is absent.

        Args:
            key: A dotted key.
            type_: Coerce to this type when given.

        Returns:
            The value.

        Raises:
            MissingKeyError: If nothing supplies the key.
        """
        value = self.get(key, _MISSING, type_=type_)
        if value is _MISSING:
            consulted = ", ".join(layer.name for layer in self._resolved.layers)
            msg = f"{key!r} is not set. Consulted: {consulted or '<no sources>'}"
            raise MissingKeyError(msg)
        return value

    def origin(self, key: str) -> Origin | None:
        """Return where a value came from.

        Args:
            key: A dotted key.

        Returns:
            Its origin, or ``None`` if the key is absent.
        """
        tracked = self._tracked(key)
        return None if tracked is None else tracked.origin

    # --------------------------------------------------------------- diagnose

    def explain(self, key: str) -> str:
        """Describe where a value came from and what it overrode.

        Every source is listed, including the ones that had nothing to say,
        because absence is information: "the environment variable is not set" is
        usually the answer someone is looking for.

        Args:
            key: A dotted key.

        Returns:
            A multi-line report.
        """
        path = canonical(key)
        dotted = join(path)
        winner = self._resolved.values.get(path)
        if winner is None:
            consulted = [
                f"      {layer.name:<30} - {'no value' if layer.found else 'not found'}"
                for layer in self._resolved.layers
            ]
            return "\n".join([f"{dotted} is not set", "  consulted:", *consulted])

        lines = [
            f"{dotted} = {sanitize(dotted, winner.value)!r}",
            f"  <- {winner.origin}",
        ]
        losers = dict(self._resolved.shadowed.get(path, ()))
        below: list[str] = []
        seen_winner = False
        for layer in self._resolved.layers:
            if not seen_winner and layer.entries.get(path) is winner:
                seen_winner = True
                continue
            tracked = losers.get(layer.name)
            if tracked is not None:
                below.append(f"      {layer.name:<30} = {sanitize(dotted, tracked.value)!r}")
            else:
                below.append(
                    f"      {layer.name:<30} - {'not set' if layer.found else 'not found'}"
                )
        # A single-source configuration has nothing under the winner, and a bare
        # "shadowed:" with no list under it reads as a truncated report.
        if below:
            lines.append("  shadowed:")
            lines.extend(below)
        return "\n".join(lines)

    def discovery_report(self) -> str:
        """Describe how configuration files were searched for.

        Returns:
            A multi-line report, or a note that discovery was not run.
        """
        if self.plan is None or self.discovery is None:
            return "no discovery was run for this configuration"
        head = (
            f"app={self.discovery.app}  prefix={self.discovery.env_prefix}  "
            f"profiles=[{', '.join(self.profiles)}]  mode={self.discovery.mode}"
        )
        return f"{head}\n{self.plan.render()}"

    def dump(self, *, reveal: bool = False) -> dict[str, Any]:
        """Return every value, redacted unless asked otherwise.

        Args:
            reveal: Skip redaction. Only ever pass this deliberately.

        Returns:
            Dotted keys to values.
        """
        out: dict[str, Any] = {}
        for path, tracked in sorted(self._resolved.values.items()):
            dotted = join(path)
            out[dotted] = tracked.value if reveal else sanitize(dotted, tracked.value)
        return out

    def as_dict(self) -> dict[str, Any]:
        """Return the resolved values as a plain nested dictionary.

        Provenance is dropped, which is the point: this is the shape to hand to
        something that only wants the data, such as a validator whence has no
        binder for.

        Returns:
            Nested plain Python values.
        """
        return unflatten({path: tracked.value for path, tracked in self._resolved.values.items()})

    def origins(self) -> dict[str, Origin]:
        """Return the origin of every value.

        Returns:
            Dotted keys to origins.
        """
        return {join(path): tracked.origin for path, tracked in self._resolved.values.items()}

    # ------------------------------------------------------------------ shape

    def bind(self, target: type, *, prefix: str | KeyPath = (), strict: bool | None = None) -> Any:
        """Bind a subtree onto a typed schema.

        Args:
            target: A dataclass, or a pydantic model when pydantic is installed.
            prefix: The subtree to read; defaults to the schema's own
                ``@settings`` prefix when it has one.
            strict: Whether a key nothing declared is an error. Defaults to
                whatever ``@settings`` recorded on the class.

        Returns:
            An instance of ``target``.

        Raises:
            BindError: If anything failed, listing every problem at once.
        """
        declared = getattr(target, "__whence_prefix__", None)
        chosen: str | KeyPath | Sequence[str] = prefix
        if prefix == () and declared is not None:
            chosen = declared
        # An empty prefix means the root, not an empty key segment: `@settings()`
        # with no argument binds the whole tree.
        if not isinstance(chosen, str):
            path: KeyPath = tuple(chosen)
        elif chosen:
            path = canonical(chosen)
        else:
            path = ()
        if strict is None:
            strict = bool(getattr(target, "__whence_strict__", True))
        return _binding.bind(
            target,
            self._resolved.values,
            prefix=path,
            shadowed=self._resolved.shadowed,
            strict=strict,
        )

    def with_fallback(self, other: "Config") -> "Config":
        """Layer another configuration underneath this one.

        This is how a library ships defaults that an application can override.
        HOCON calls it ``withFallback``, and figment separates it from ``merge``
        because "replace" and "fill in the gaps" are genuinely different
        operations. whence has both: the source chain merges, and this fills
        gaps -- a key already set here keeps its value and its origin.

        Args:
            other: The configuration to fall back to.

        Returns:
            A new configuration.
        """
        merged = resolve([*self._resolved.layers, *other._resolved.layers])
        return Config(merged, plan=self.plan, profiles=self.profiles, discovery=self.discovery)

    def __repr__(self) -> str:
        """Summarise without printing any values."""
        return (
            f"Config({len(self._resolved.values)} keys from "
            f"{len(self._resolved.layers)} sources, profiles={list(self.profiles)})"
        )
