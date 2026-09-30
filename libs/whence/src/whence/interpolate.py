r"""``${...}`` placeholders, resolved over the merged tree.

Resolution happens *after* the merge rather than inside each file, which is what
lets a base file reference a key that a higher-precedence source supplies. It
also happens through a real scanner rather than ``string.Template`` or
``str.format``: those offer no defaults, no nesting and no escaping, and
``safe_substitute`` silently leaves unresolved text in place -- which is exactly
how a literal ``${db.password}`` reaches a database driver.

Supported forms:

* ``${a.b}``           another configuration key; missing is an error
* ``${a.b:-default}``  POSIX-style, so ``${url:-redis://h:6379}`` is unambiguous
* ``${?a.b}``          optional: the key disappears entirely when undefined
* ``${env:VAR}``       an environment variable, ignoring the prefix
* ``${file:/path}``    the contents of a file, for inline container secrets
* ``\\${literal}``      an escaped placeholder

There is deliberately no global "ignore unresolvable" switch. It is the setting
that converts a startup failure into a literal placeholder reaching production.
"""

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import InterpolationError
from .keys import KeyPath, canonical, join
from .origin import Tracked

__all__ = ["MAX_DEPTH", "interpolate"]

MAX_DEPTH = 16
"""How deep placeholder expansion may nest before it is called a cycle."""

_DROP = object()


def _scan(text: str) -> list[tuple[str, bool]]:
    """Split text into literal and placeholder chunks."""
    parts: list[tuple[str, bool]] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and text[i + 1 : i + 2] == "$":
            buf.append("$")
            i += 2
            continue
        if char == "$" and text[i + 1 : i + 2] == "{":
            depth = 1
            j = i + 2
            while j < len(text) and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                buf.append(char)
                i += 1
                continue
            if buf:
                parts.append(("".join(buf), False))
                buf = []
            parts.append((text[i + 2 : j - 1], True))
            i = j
            continue
        buf.append(char)
        i += 1
    if buf:
        parts.append(("".join(buf), False))
    return parts


def _lookup(
    expr: str,
    values: Mapping[KeyPath, Tracked],
    environ: Mapping[str, str],
    resolve: Callable[[KeyPath, str, int], object],
    depth: int,
    active: set[KeyPath],
) -> object:
    """Resolve one placeholder body."""
    optional = expr.startswith("?")
    if optional:
        expr = expr[1:]
    body, sep, fallback = expr.partition(":-")
    body = body.strip()

    found: object
    if body.startswith("env:"):
        found = environ.get(body[4:].strip())
    elif body.startswith("file:"):
        target = Path(body[5:].strip())
        try:
            found = target.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            found = None
    else:
        path = canonical(body)
        # A key referring to itself is "not available" rather than an error when
        # the reference is optional or carries a default. `port = ${?port}` is a
        # natural thing to write and must not explode; only a genuine cycle with
        # no way out is reported as one.
        if path in active and (optional or sep):
            found = None
        else:
            tracked = values.get(path)
            found = None if tracked is None else tracked.value
            if isinstance(found, str) and "${" in found:
                found = resolve(path, found, depth + 1)

    if found is not None:
        return found
    if sep:
        return fallback
    if optional:
        return _DROP
    msg = (
        f"cannot resolve ${{{expr}}}: nothing supplies {body!r}. "
        "Give it a value, a default with `:-`, or make it optional with `${?...}`"
    )
    raise InterpolationError(msg)


def interpolate(
    values: Mapping[KeyPath, Tracked],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[KeyPath, Tracked]:
    """Expand every placeholder in a merged tree.

    Args:
        values: The merged values.
        environ: The environment for ``${env:...}``; defaults to ``os.environ``.

    Returns:
        The same tree with placeholders expanded. Keys whose entire value was an
        unresolved ``${?...}`` are removed.

    Raises:
        InterpolationError: On an unresolvable placeholder or a reference cycle.
    """
    env = os.environ if environ is None else environ
    active: set[KeyPath] = set()

    def resolve(path: KeyPath, text: str, depth: int = 0) -> object:
        if depth > MAX_DEPTH or path in active:
            chain = " -> ".join(join(p) for p in (*active, path))
            msg = f"circular placeholder reference: {chain}"
            raise InterpolationError(msg)
        active.add(path)
        try:
            parts = _scan(text)
            if len(parts) == 1 and parts[0][1]:
                return _lookup(parts[0][0], values, env, resolve, depth, active)
            out: list[str] = []
            for chunk, is_placeholder in parts:
                if not is_placeholder:
                    out.append(chunk)
                    continue
                piece = _lookup(chunk, values, env, resolve, depth, active)
                out.append("" if piece is _DROP else str(piece))
            return "".join(out)
        finally:
            active.discard(path)

    result: dict[KeyPath, Tracked] = {}
    for path, tracked in values.items():
        raw = tracked.value
        if not isinstance(raw, str) or "${" not in raw:
            result[path] = tracked
            continue
        expanded = resolve(path, raw)
        if expanded is _DROP:
            continue
        # A value produced by expanding a template records the template as its parent.
        result[path] = tracked if expanded == raw else Tracked(expanded, tracked.origin.derived())
    return result
