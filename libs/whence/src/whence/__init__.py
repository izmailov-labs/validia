"""whence -- typed configuration that remembers where it came from.

Every configuration library can tell you a value. whence can tell you *why* it
has that value: which file, which line, which profile, and what it overrode.

    >>> from whence import Config
    >>> cfg = Config.from_mapping({"db": {"host": "localhost"}})
    >>> cfg.get("db.host")
    'localhost'

Loading is synchronous and happens once, before the application runs: there is
no event loop to protect at that point, so there is nothing for an ``await`` to
yield to. A source that reaches the network simply blocks the startup it is
already part of.

The public API is everything listed in ``__all__``; anything else is internal
and may change without a major version bump.
"""

from importlib.metadata import PackageNotFoundError, version

from .binding import Binder, Problem, bind, render_problems
from .chain import SourceChain
from .config import Config
from .decorators import (
    Injected,
    Value,
    configure,
    current_config,
    from_config,
    load_settings,
    settings,
)
from .discovery import Discovery, DiscoveryPlan, PyProjectSource, Step
from .errors import (
    AmbiguousConfigError,
    BindError,
    ConfigError,
    FormatError,
    InterpolationError,
    MissingConfigError,
    MissingKeyError,
    SecretError,
    UnboundKeyError,
    WhenceError,
)
from .keys import KeyPath, canonical, env_name, key_from_env
from .origin import Origin, RelativePath, Tracked, origin_of, unwrap
from .profiles import active_profiles, expand_groups
from .secret import MASK, Secret, is_sensitive, sanitize, unlock_secrets
from .sources import (
    ArgvSource,
    DotEnvSource,
    EnvSource,
    FileSource,
    MappingSource,
    SecretsDirSource,
    Source,
)
from .tree import Layer, Resolved

__all__ = [
    "MASK",
    "AmbiguousConfigError",
    "ArgvSource",
    "BindError",
    "Binder",
    "Config",
    "ConfigError",
    "Discovery",
    "DiscoveryPlan",
    "DotEnvSource",
    "EnvSource",
    "FileSource",
    "FormatError",
    "Injected",
    "InterpolationError",
    "KeyPath",
    "Layer",
    "MappingSource",
    "MissingConfigError",
    "MissingKeyError",
    "Origin",
    "Problem",
    "PyProjectSource",
    "RelativePath",
    "Resolved",
    "Secret",
    "SecretError",
    "SecretsDirSource",
    "Source",
    "SourceChain",
    "Step",
    "Tracked",
    "UnboundKeyError",
    "Value",
    "WhenceError",
    "__version__",
    "active_profiles",
    "bind",
    "canonical",
    "configure",
    "current_config",
    "env_name",
    "expand_groups",
    "from_config",
    "is_sensitive",
    "key_from_env",
    "load_settings",
    "origin_of",
    "render_problems",
    "sanitize",
    "settings",
    "unlock_secrets",
    "unwrap",
]

try:
    __version__ = version("whence")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0.dev0"
