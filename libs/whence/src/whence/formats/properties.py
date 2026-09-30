r"""Java ``.properties``, parsed by hand so that positions survive.

Spring had to fork the ordinary parser into ``OriginTrackedPropertiesLoader``
for exactly this reason: line numbers are only available to whoever does the
scanning. The format is small enough that owning it is cheaper than not.

Implements the rules from ``java.util.Properties``: ``#`` and ``!`` comments,
``=``, ``:`` or bare whitespace as the separator, backslash line continuations,
and ``\\uXXXX`` escapes. CRLF is handled by splitting on universal newlines,
which matters because these files are frequently authored on Windows.
"""

from ..errors import FormatError
from ..keys import KeyPath, canonical
from ..origin import Origin, Tracked

__all__ = ["load_properties"]

_SIMPLE = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}


def _unescape(raw: str, locator: str, line: int) -> str:
    r"""Expand backslash escapes, including ``\\uXXXX``."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char != "\\":
            out.append(char)
            i += 1
            continue
        i += 1
        if i >= len(raw):
            break
        marker = raw[i]
        if marker == "u":
            digits = raw[i + 1 : i + 5]
            if len(digits) < 4 or not all(d in "0123456789abcdefABCDEF" for d in digits):
                msg = f"{locator}:{line}: malformed \\u escape"
                raise FormatError(msg)
            out.append(chr(int(digits, 16)))
            i += 5
        else:
            out.append(_SIMPLE.get(marker, marker))
            i += 1
    return "".join(out)


def _split(logical: str) -> tuple[str, str]:
    """Split a logical line at the first unescaped separator."""
    i = 0
    while i < len(logical):
        char = logical[i]
        if char == "\\":
            i += 2
            continue
        if char in "=:":
            return logical[:i], logical[i + 1 :].lstrip()
        if char.isspace():
            rest = logical[i:].lstrip()
            if rest[:1] in {"=", ":"}:
                return logical[:i], rest[1:].lstrip()
            return logical[:i], rest
        i += 1
    return logical, ""


def load_properties(text: str, locator: str) -> dict[KeyPath, Tracked]:
    """Parse ``.properties`` text into tracked values with line numbers.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Flat key paths to tracked values.

    Raises:
        FormatError: If an escape sequence is malformed.
    """
    out: dict[KeyPath, Tracked] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start = i
        line = lines[i]
        stripped = line.lstrip()
        i += 1
        if not stripped or stripped[0] in "#!":
            continue
        # Captured now: the continuation loop below overwrites `stripped`.
        column = len(line) - len(stripped) + 1
        # A line ending in an odd number of backslashes continues onto the next.
        while stripped.endswith("\\") and (len(stripped) - len(stripped.rstrip("\\"))) % 2 == 1:
            stripped = stripped[:-1] + (lines[i].lstrip() if i < len(lines) else "")
            i += 1
        raw_key, raw_value = _split(stripped)
        key = _unescape(raw_key, locator, start + 1).strip()
        if not key:
            continue
        out[canonical(key)] = Tracked(
            _unescape(raw_value, locator, start + 1),
            Origin("file", locator, start + 1, column),
        )
    return out
