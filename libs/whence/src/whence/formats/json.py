"""JSON via the standard library.

``json`` exposes no positions either, so origins are file-level. Errors do carry
the decoder's own line and column, which is the part that matters when a file
fails to parse at all.
"""

import json as _json
from collections.abc import Mapping

from ..errors import FormatError
from ..keys import KeyPath, flatten
from ..origin import Origin, Tracked

__all__ = ["load_json"]


def load_json(text: str, locator: str) -> dict[KeyPath, Tracked]:
    """Parse JSON text into tracked values.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Flat key paths to tracked values.

    Raises:
        FormatError: If the document does not parse, or is not an object.
    """
    try:
        data = _json.loads(text)
    except _json.JSONDecodeError as exc:
        msg = f"{locator}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        raise FormatError(msg) from exc
    if not isinstance(data, Mapping):
        msg = f"{locator}: top level of a JSON config must be an object"
        raise FormatError(msg)
    origin = Origin("file", locator)
    return {path: Tracked(value, origin) for path, value in flatten(data).items()}
