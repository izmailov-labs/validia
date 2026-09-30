"""TOML via the standard library's ``tomllib``.

``tomllib`` reports no positions, so origins here are file-level. That is an
honest limitation rather than an oversight: recovering line numbers would mean
reimplementing the parser, which is the price Spring paid for
``OriginTrackedPropertiesLoader``.
"""

import tomllib

from ..errors import FormatError
from ..keys import KeyPath, flatten
from ..origin import Origin, Tracked

__all__ = ["load_toml"]


def load_toml(text: str, locator: str) -> dict[KeyPath, Tracked]:
    """Parse TOML text into tracked values.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Flat key paths to tracked values.

    Raises:
        FormatError: If the document does not parse.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = f"{locator}: invalid TOML: {exc}"
        raise FormatError(msg) from exc
    origin = Origin("file", locator)
    return {path: Tracked(value, origin) for path, value in flatten(data).items()}
