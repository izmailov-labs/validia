r"""``.env`` files, parsed by hand with line numbers.

There is no specification for this format. Three attempts exist and none was
adopted; python-dotenv's own documentation says "the format is not formally
specified and still improves over time". Implementations disagree about inline
comments, escape expansion, backticks and command substitution, so the only
responsible thing is to state the rules and test them.

whence implements the intersection that every major implementation agrees on:

* ``KEY=value``, with an optional ``export`` prefix (stripped by all four major
  implementations, documented by none of them any more).
* ``#`` starts a comment on its own line, or mid-value when it follows
  whitespace. Inside quotes it is literal. The preceding space is required, not
  optional -- without it ``color=#ff0000`` loses its value.
* Single quotes are literal. Double quotes expand ``\\n``, ``\\t``, ``\\r`` and
  ``\\\\``. Both preserve surrounding whitespace; unquoted values are stripped.
* Quoted values may span lines.
* CRLF is handled, because these files are routinely authored on Windows.

Variable expansion is deliberately *not* done here. It happens later, over the
merged tree, so a ``.env`` value can reference a key that a different source
supplied -- which file-local expansion cannot do.
"""

from collections.abc import Mapping
from pathlib import Path

from ..errors import FormatError
from ..keys import KeyPath, canonical, key_from_env, validate_prefix
from ..origin import Origin, Tracked
from ..tree import Layer

__all__ = ["DotEnvSource", "parse_dotenv"]

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "`": "`"}


def _unquote(raw: str, quote: str) -> str:
    """Expand escapes inside a double-quoted or backtick-quoted value."""
    if quote == "'":
        return raw
    out: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(_ESCAPES.get(raw[i + 1], "\\" + raw[i + 1]))
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def _strip_comment(value: str) -> str:
    """Drop an unquoted trailing comment, which must follow whitespace."""
    for i, char in enumerate(value):
        # The preceding space is required, not optional: without it `color=#ff0000`
        # loses its value. Full-line comments are handled before this is reached.
        if char == "#" and i > 0 and value[i - 1].isspace():
            return value[:i]
    return value


def parse_dotenv(text: str, locator: str) -> dict[str, Tracked]:
    """Parse ``.env`` text into variable names with positions.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Variable names to tracked string values.

    Raises:
        FormatError: If a quoted value is never closed.
    """
    out: dict[str, Tracked] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start = i
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = stripped.removeprefix("export ").lstrip()
        name, sep, rest = stripped.partition("=")
        if not sep:
            continue
        name = name.strip()
        if not name:
            continue
        rest = rest.lstrip()
        quote = rest[0] if rest[:1] in {'"', "'", "`"} else ""
        if not quote:
            value = _strip_comment(rest).strip()
        else:
            body = rest[1:]
            chunks: list[str] = []
            while True:
                end = _find_close(body, quote)
                if end is not None:
                    chunks.append(body[:end])
                    break
                chunks.append(body)
                if i >= len(lines):
                    msg = f"{locator}:{start + 1}: unterminated {quote} quoted value"
                    raise FormatError(msg)
                body = lines[i]
                i += 1
            value = _unquote("\n".join(chunks), quote)
        column = len(line) - len(line.lstrip()) + 1
        out[name] = Tracked(value, Origin("dotenv", locator, start + 1, column))
    return out


def _find_close(body: str, quote: str) -> int | None:
    """Return the index of the closing quote, honouring backslash escapes."""
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == quote:
            return i
        i += 1
    return None


class DotEnvSource:
    """Reads a ``.env`` file and maps its variables like the environment.

    Args:
        path: The file to read.
        prefix: The environment prefix, applied exactly as ``EnvSource`` does so
            that ``.env`` and the real environment stay interchangeable.
        name: The layer name.
        aliases: Explicit variable-to-key overrides.
    """

    def __init__(
        self,
        path: Path | str,
        prefix: str = "",
        *,
        name: str = "dotenv",
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        """Build a ``.env`` source."""
        self.name = name
        self.path = Path(path)
        self.prefix = validate_prefix(prefix)
        self._aliases = dict(aliases or {})

    def load(self) -> Layer:
        """Read the file, if it exists.

        Returns:
            The layer, with ``found=False`` when the file is absent.

        Raises:
            FormatError: If the file exists but cannot be parsed.
        """
        if not self.path.is_file():
            return Layer(self.name, {}, found=False)
        text = self.path.read_text(encoding="utf-8-sig")
        entries: dict[KeyPath, Tracked] = {}
        for raw_name, tracked in parse_dotenv(text, str(self.path)).items():
            alias = self._aliases.get(raw_name)
            path = canonical(alias) if alias else key_from_env(raw_name, self.prefix)
            if path is not None:
                entries[path] = tracked
        return Layer(self.name, entries, found=True)
