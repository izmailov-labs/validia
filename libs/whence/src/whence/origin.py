"""Provenance: where a value came from, and what it displaced.

``Origin`` is the spine of the library. It is attached at parse time, survives
the merge, the interpolation pass and the binder, and ends up in the error
message. Everything else here exists to make carrying it free.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

__all__ = ["Origin", "RelativePath", "Tracked", "origin_of", "unwrap"]


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a single configuration value came from.

    Attributes:
        source: The name of the source that produced it, such as ``"env"`` or
            ``"file"``. Matches the name the value's source carries in the chain.
        locator: The concrete location within that source: a file path, an
            environment variable name, or ``"<defaults>"``.
        line: One-based line number, when the parser reports positions.
        column: One-based column number, when the parser reports positions.
        profile: The profile whose overlay contributed the value, if any.
        parent: The origin this one was derived from -- the variable behind a
            ``_FILE`` indirection, or the template behind an interpolated value.
    """

    source: str
    locator: str
    line: int | None = None
    column: int | None = None
    profile: str | None = None
    parent: "Origin | None" = None

    def __str__(self) -> str:
        """Render as ``locator:line:column [profile=...]``."""
        text = self.locator
        if self.line is not None:
            text = f"{text}:{self.line}"
            if self.column is not None:
                text = f"{text}:{self.column}"
        if self.profile is not None:
            text = f"{text} [profile={self.profile}]"
        if self.parent is not None:
            text = f"{text} <- {self.parent}"
        return text

    def derived(self, **changes: Any) -> "Origin":
        """Return a copy that records this origin as its parent.

        Args:
            **changes: Fields to override on the copy.

        Returns:
            A new origin whose ``parent`` is this one.
        """
        return replace(self, parent=self, **changes)


@dataclass(frozen=True, slots=True)
class Tracked:
    """A value paired with its origin.

    Equality and hashing delegate to the wrapped value on purpose. That is what
    makes provenance retrofittable instead of viral: every consumer that
    compares, hashes or formats a value keeps working unchanged, and only code
    that explicitly asks for ``.origin`` pays any attention to it. A sidecar
    ``{key: origin}`` map loses provenance at every transformation, and a ``str``
    subclass loses it at the first f-string.

    Attributes:
        value: The underlying value.
        origin: Where it came from.
    """

    value: Any
    origin: Origin = field(compare=False, hash=False)

    def __eq__(self, other: object) -> bool:
        """Compare against the wrapped value, tracked or not."""
        return bool(self.value == unwrap(other))

    def __hash__(self) -> int:
        """Hash as the wrapped value does."""
        return hash(self.value)

    def __str__(self) -> str:
        """Render as the wrapped value does."""
        return str(self.value)

    def __repr__(self) -> str:
        """Show the value and its origin."""
        return f"Tracked({self.value!r} <- {self.origin})"

    def __bool__(self) -> bool:
        """Report the truthiness of the wrapped value."""
        return bool(self.value)


def unwrap(obj: object) -> Any:
    """Strip tracking from a value, recursively through containers.

    Args:
        obj: A tracked value, a plain value, or a container of either.

    Returns:
        The same shape with every :class:`Tracked` replaced by its value.
    """
    if isinstance(obj, Tracked):
        return unwrap(obj.value)
    if isinstance(obj, dict):
        return {k: unwrap(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unwrap(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(unwrap(v) for v in obj)
    return obj


def origin_of(obj: object) -> Origin | None:
    """Return the origin of a value, or ``None`` if it carries none.

    Args:
        obj: Any value.

    Returns:
        The origin, when ``obj`` is :class:`Tracked`.
    """
    return obj.origin if isinstance(obj, Tracked) else None


class RelativePath(Path):
    """A path resolved against the file that declared it, not the process cwd.

    ``cert = "./ca.pem"`` inside ``deploy/app.yaml`` means ``deploy/ca.pem``,
    wherever the program happens to be run from. That is only expressible
    because the value remembers where it came from; figment calls the same idea
    ``RelativePathBuf`` and nothing in Python offers it.

    An absolute value is left alone, and a value from a source with no file
    behind it -- an environment variable, say -- is resolved against the cwd,
    because there is nothing better to resolve it against.
    """

    __slots__ = ()

    @classmethod
    def resolve_against(cls, value: object, origin: "Origin | None") -> Path:
        """Resolve a path value against its origin's directory.

        Args:
            value: The raw path value.
            origin: Where the value came from.

        Returns:
            An absolute path where the origin names a file, and the value
            unchanged otherwise.
        """
        path = Path(str(value))
        if path.is_absolute() or origin is None or origin.source != "file":
            return path
        base = Path(origin.locator.split(" [")[0]).parent
        return base / path
