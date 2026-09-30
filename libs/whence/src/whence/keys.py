"""Canonical key paths, and the bijective mapping to environment variables.

Java frameworks need fuzzy "relaxed binding" because ``app.max-retries`` must
match ``APP_MAXRETRIES``: their field names are camelCase and environment
variables cannot hold ``.`` or ``-``. Python does not have that problem.
snake_case maps to SCREAMING_SNAKE and back without loss, so whence has exactly
one lookup path and no ambiguity -- and a near-miss spelling can be reported
instead of silently accepted.

The canonical form is lowercase, dot-separated, with ``-`` folded to ``_``.
The environment spelling is a literal prefix followed by segments joined with
``__``; a single ``_`` always stays inside a segment, so ``APP_DB__MAX_RETRIES``
is unambiguously ``db.max_retries`` and never ``db.max.retries``.
"""

from collections.abc import Iterable, Mapping

from .errors import ConfigError

__all__ = [
    "NESTING",
    "KeyPath",
    "canonical",
    "default_prefix",
    "env_name",
    "join",
    "key_from_env",
    "validate_prefix",
]

type KeyPath = tuple[str, ...]

NESTING = "__"
"""The delimiter that separates nesting levels inside an environment variable."""


def canonical(key: str | Iterable[str]) -> KeyPath:
    """Normalise a dotted key or a sequence of segments into a key path.

    Args:
        key: ``"db.max-retries"``, ``"DB.MaxRetries"`` or ``("db", "max_retries")``.

    Returns:
        The canonical path, lowercase with ``-`` folded to ``_``.

    Raises:
        ConfigError: If the key is empty or contains an empty segment.
    """
    parts = key.split(".") if isinstance(key, str) else list(key)
    if not parts:
        msg = "empty configuration key"
        raise ConfigError(msg)
    out: list[str] = []
    for part in parts:
        segment = part.strip().replace("-", "_").lower()
        if not segment:
            msg = f"empty segment in configuration key {key!r}"
            raise ConfigError(msg)
        out.append(segment)
    return tuple(out)


def join(path: KeyPath) -> str:
    """Render a key path in its canonical dotted form.

    Args:
        path: The key path.

    Returns:
        ``"db.max_retries"``.
    """
    return ".".join(path)


def default_prefix(app: str) -> str:
    """Derive the environment prefix for an application name.

    Args:
        app: The application name, such as ``"myapp"``.

    Returns:
        ``"MYAPP_"``.
    """
    return f"{canonical(app)[0].upper()}_"


def validate_prefix(prefix: str) -> str:
    """Check that a prefix can be stripped before nesting is parsed.

    A prefix containing the nesting delimiter cannot be removed unambiguously:
    given ``MY__APP_`` the name ``MY__APP_DB__HOST`` could be stripped at either
    ``__``. pydantic-settings carries a live bug for exactly this reason, so
    whence rejects it at construction instead.

    Args:
        prefix: The candidate prefix.

    Returns:
        The prefix, unchanged.

    Raises:
        ConfigError: If the prefix contains the nesting delimiter.
    """
    if NESTING in prefix:
        msg = (
            f"environment prefix {prefix!r} contains the nesting delimiter {NESTING!r}, "
            "which makes stripping it ambiguous; use a single trailing underscore"
        )
        raise ConfigError(msg)
    return prefix


def env_name(path: KeyPath, prefix: str = "") -> str:
    """Render the one environment variable name that maps to a key path.

    Args:
        path: The canonical key path.
        prefix: The environment prefix, already validated.

    Returns:
        ``"MYAPP_DB__MAX_RETRIES"``.
    """
    return prefix + NESTING.join(segment.upper() for segment in path)


def key_from_env(name: str, prefix: str = "") -> KeyPath | None:
    """Map an environment variable name back to a key path.

    Args:
        name: The variable name as it appears in the environment.
        prefix: The environment prefix, already validated.

    Returns:
        The key path, or ``None`` if the name does not carry the prefix or has
        an empty segment.
    """
    if prefix and not name.startswith(prefix):
        return None
    body = name[len(prefix) :]
    if not body:
        return None
    segments = body.split(NESTING)
    if any(not segment for segment in segments):
        return None
    return tuple(segment.lower() for segment in segments)


def suggest(unknown: KeyPath, known: Iterable[KeyPath], limit: int = 1) -> list[str]:
    """Find declared keys that look like a misspelling of an unknown one.

    Args:
        unknown: The key nothing consumed.
        known: Every key the schema declares.
        limit: How many suggestions to return.

    Returns:
        Dotted key names, closest first, possibly empty.
    """
    import difflib

    target = join(unknown)
    pool = [join(k) for k in known]
    return difflib.get_close_matches(target, pool, n=limit, cutoff=0.7)


def flatten(data: Mapping[str, object], prefix: KeyPath = ()) -> dict[KeyPath, object]:
    """Flatten a nested mapping into canonical key paths.

    Args:
        data: A nested mapping, as a format loader returns it.
        prefix: The path this mapping sits at.

    Returns:
        A flat mapping from key path to leaf value. Nested mappings are walked;
        every other value, lists included, is a leaf.
    """
    out: dict[KeyPath, object] = {}
    for raw, value in data.items():
        path = (*prefix, *canonical(str(raw)))
        if isinstance(value, Mapping) and value:
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out
