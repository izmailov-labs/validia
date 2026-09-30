"""The whence exception hierarchy.

Every error carries enough context to act on without reading the library's
source: which key, which file, which line. Nothing here ever interpolates a
configuration *value* into its message -- a rejected value may be a secret, and
an exception is the most widely logged object in a program.
"""

__all__ = [
    "AmbiguousConfigError",
    "BindError",
    "ConfigError",
    "FormatError",
    "InterpolationError",
    "MissingConfigError",
    "MissingKeyError",
    "SecretError",
    "UnboundKeyError",
    "WhenceError",
]


class WhenceError(Exception):
    """Base class for every error whence raises."""


class ConfigError(WhenceError):
    """The configuration itself is wrong: bad discovery, bad wiring, bad source."""


class MissingConfigError(ConfigError):
    """A configuration file that was named explicitly does not exist."""


class AmbiguousConfigError(ConfigError):
    """Two files in one search root both satisfy the same logical name.

    Raised rather than resolved by preference, because silently picking one is
    the failure mode that took Spring Boot years to specify.
    """


class FormatError(ConfigError):
    """A configuration file could not be parsed."""


class MissingKeyError(WhenceError, KeyError):
    """A required key is absent from every source."""

    def __str__(self) -> str:
        """Render without ``KeyError``'s surrounding quotes."""
        return str(self.args[0]) if self.args else ""


class InterpolationError(ConfigError):
    """A ``${...}`` placeholder could not be resolved."""


class UnboundKeyError(ConfigError):
    """Configuration supplied a key that the target schema does not declare."""


class BindError(ConfigError):
    """One or more values could not be bound to the target schema."""


class SecretError(WhenceError):
    """A secret was read outside an explicit ``unlock_secrets()`` scope."""
