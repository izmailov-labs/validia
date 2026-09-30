"""Secret values, and the sanitizer that runs before anything is printed.

The moment a configuration library grows a ``dump`` command it becomes an
exfiltration tool. That is not hypothetical: .NET's ``GetDebugView`` ships
unredacted and its own documentation tells you to hide it behind a development
flag, and pydantic-settings' debug output says in its merge commit that it "may
contain secrets". whence redacts by default and makes the caller opt out.

:class:`Secret` additionally refuses to be read at all outside an explicit
``unlock_secrets()`` scope. SmallRye's ``SecretKeys.doUnlocked`` does the same,
and it costs almost nothing to turn an accidental exposure into a loud error.
"""

import fnmatch
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .errors import SecretError

__all__ = ["MASK", "SENSITIVE", "Secret", "is_sensitive", "sanitize", "unlock_secrets"]

MASK = "********"
"""What a redacted value renders as."""

SENSITIVE: tuple[str, ...] = (
    "*password*",
    "*secret*",
    "*token*",
    "*api_key*",
    "*apikey*",
    "*credential*",
    "*private_key*",
)
"""Key patterns redacted by default, matched case-insensitively."""

_UNLOCKED: ContextVar[bool] = ContextVar("whence_secrets_unlocked", default=False)


class Secret:
    """A string that will not reveal itself by accident.

    ``repr`` and ``str`` both render the mask, so a secret cannot reach a log
    line, a traceback or an f-string through inattention. Reading the real value
    needs :func:`unlock_secrets`, which makes the intent explicit and greppable.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Wrap a string.

        Args:
            value: The sensitive value.
        """
        self._value = value

    def reveal(self) -> str:
        """Return the underlying string.

        Returns:
            The real value.

        Raises:
            SecretError: If called outside an ``unlock_secrets()`` scope.
        """
        if not _UNLOCKED.get():
            msg = (
                "reading a Secret requires an explicit scope: "
                "`with unlock_secrets(): ...` around the call"
            )
            raise SecretError(msg)
        return self._value

    def __repr__(self) -> str:
        """Render the mask, never the value."""
        return f"Secret({MASK})"

    def __str__(self) -> str:
        """Render the mask, never the value."""
        return MASK

    def __eq__(self, other: object) -> bool:
        """Compare two secrets without revealing either."""
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        """Hash the underlying value."""
        return hash(self._value)

    def __bool__(self) -> bool:
        """Report whether the underlying string is non-empty."""
        return bool(self._value)


@contextmanager
def unlock_secrets() -> Generator[None]:
    """Permit :meth:`Secret.reveal` for the duration of the block.

    The flag lives in a :class:`~contextvars.ContextVar`, so it does not leak
    across tasks and a concurrent request cannot ride on another's unlock.

    Yields:
        Nothing.
    """
    token = _UNLOCKED.set(True)
    try:
        yield
    finally:
        _UNLOCKED.reset(token)


def is_sensitive(key: str, patterns: Sequence[str] = SENSITIVE) -> bool:
    """Report whether a key name looks like it holds a secret.

    Args:
        key: A dotted key name.
        patterns: Glob patterns to match against, case-insensitively.

    Returns:
        True when any pattern matches.
    """
    lowered = key.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in patterns)


def sanitize(key: str, value: Any, patterns: Sequence[str] = SENSITIVE) -> Any:
    """Redact a value if its key or its type says it is sensitive.

    A :class:`Secret` is masked whatever it is called, and a value is masked
    when its key matches. Both directions matter: convict masks by declared
    sensitivity, Spring masks by key pattern, and each misses what the other
    catches.

    Args:
        key: The dotted key name.
        value: The value.
        patterns: Key patterns to treat as sensitive.

    Returns:
        Either the value or :data:`MASK`.
    """
    if isinstance(value, Secret) or is_sensitive(key, patterns):
        return MASK
    return value
