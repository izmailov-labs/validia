"""Turning strings into the types a schema asks for.

No message in this module ever contains the value it rejected. A rejected value
may be a secret, and an exception is the most widely logged object in a program;
the standard library's own conversion errors quote the input, so they are caught
and replaced rather than passed through.

Environment variables and ``.properties`` files carry only strings, so some
coercion is unavoidable. The rule whence follows is that coercion is **never a
global guess**: a list is JSON when the string opens with ``[`` and
comma-separated otherwise, and everything else is driven by the declared field
type. The three conventions in the wild -- JSON, comma-separated and indexed
keys -- are irreconcilable, so guessing between them by inspection is how a
value silently becomes the wrong shape.
"""

import datetime as dt
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from ..secret import Secret

__all__ = ["TRUE", "coerce", "is_optional", "strip_annotated"]

TRUE = frozenset({"1", "true", "yes", "on", "y", "t"})
"""Strings that mean ``True``; everything else that parses means ``False``."""

FALSE = frozenset({"0", "false", "no", "off", "n", "f", ""})


class CoercionError(ValueError):
    """A value could not be converted to the declared type."""


def strip_annotated(annotation: Any) -> Any:
    """Return the type an annotation declares, without its metadata.

    ``Annotated[int, Value("x")]`` declares an ``int``; the metadata is for
    whoever put it there. Stripping is recursive because nesting is legal.

    Args:
        annotation: A type annotation, annotated or not.

    Returns:
        The underlying type.
    """
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def is_optional(annotation: Any) -> bool:
    """Report whether an annotation admits ``None``.

    Args:
        annotation: A type annotation.

    Returns:
        True for ``X | None`` and ``Optional[X]``.
    """
    annotation = strip_annotated(annotation)
    return get_origin(annotation) in (Union, UnionType) and type(None) in get_args(annotation)


def _non_none(annotation: Any) -> Any:
    """Strip ``None`` from an optional annotation."""
    args = [a for a in get_args(annotation) if a is not type(None)]
    return args[0] if len(args) == 1 else annotation


def _to_bool(value: Any) -> bool:
    """Parse a boolean from a string or a number."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    msg = f"expected a boolean; accepted spellings are {sorted(TRUE | FALSE - {''})}"
    raise CoercionError(msg)


def _to_timedelta(value: Any) -> dt.timedelta:
    """Parse a duration from seconds or a ``1h30m`` style string."""
    if isinstance(value, dt.timedelta):
        return value
    if isinstance(value, (int, float)):
        return dt.timedelta(seconds=float(value))
    text = str(value).strip().lower()
    try:
        return dt.timedelta(seconds=float(text))
    except ValueError:
        pass
    units = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    total, number = 0.0, ""
    i = 0
    while i < len(text):
        if text[i].isdigit() or text[i] == ".":
            number += text[i]
            i += 1
            continue
        unit = text[i : i + 2] if text[i : i + 2] in units else text[i]
        if unit not in units or not number:
            msg = "expected a duration such as '30s', '1h30m' or a number of seconds"
            raise CoercionError(msg)
        total += float(number) * units[unit]
        number = ""
        i += len(unit)
    if number:
        total += float(number)
    return dt.timedelta(seconds=total)


def _split_list(text: str) -> list[Any]:
    """Split a list from JSON or from commas."""
    stripped = text.strip()
    if stripped.startswith("["):
        loaded = json.loads(stripped)
        if isinstance(loaded, list):
            return loaded
    return [part.strip() for part in stripped.split(",") if part.strip()]


_SCALARS: Mapping[Any, Callable[[Any], Any]] = {
    bool: _to_bool,
    int: lambda v: v if isinstance(v, int) and not isinstance(v, bool) else int(str(v).strip()),
    float: lambda v: float(v) if isinstance(v, (int, float)) else float(str(v).strip()),
    str: lambda v: v if isinstance(v, str) else str(v),
    Path: lambda v: v if isinstance(v, Path) else Path(str(v)),
    uuid.UUID: lambda v: v if isinstance(v, uuid.UUID) else uuid.UUID(str(v)),
    dt.timedelta: _to_timedelta,
    dt.datetime: lambda v: v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v)),
    dt.date: lambda v: v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)),
    Secret: lambda v: v if isinstance(v, Secret) else Secret(str(v)),
}


def coerce(value: Any, annotation: Any) -> Any:
    """Convert a loaded value to the type a field declares.

    Args:
        value: The value as a source produced it.
        annotation: The declared type.

    Returns:
        The converted value.

    Raises:
        CoercionError: If the value cannot be converted.
    """
    annotation = strip_annotated(annotation)
    if annotation is Any or annotation is None:
        return value
    if is_optional(annotation):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return coerce(value, _non_none(annotation))

    origin = get_origin(annotation)
    if origin is Literal:
        allowed = get_args(annotation)
        for option in allowed:
            if value == option or str(value) == str(option):
                return option
        msg = f"expected one of {list(allowed)}"
        raise CoercionError(msg)

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        for member in annotation:
            if value is member or value == member.value or str(value) == str(member.value):
                return member
        try:
            return annotation[str(value)]
        except KeyError:
            msg = f"expected one of {[m.value for m in annotation]}"
            raise CoercionError(msg) from None

    if origin in (list, tuple, set, frozenset):
        items = _split_list(value) if isinstance(value, str) else list(value)
        args = get_args(annotation)
        inner = args[0] if args and args[0] is not Ellipsis else Any
        converted = [coerce(item, inner) for item in items]
        return origin(converted) if origin is not list else converted

    if origin is dict:
        raw = json.loads(value) if isinstance(value, str) else value
        if not isinstance(raw, Mapping):
            msg = "expected a mapping"
            raise CoercionError(msg)
        args = get_args(annotation)
        kt, vt = (*args, Any, Any)[:2]
        return {coerce(k, kt): coerce(v, vt) for k, v in raw.items()}

    convert = _SCALARS.get(annotation)
    if convert is not None:
        try:
            return convert(value)
        except CoercionError:
            raise
        except (TypeError, ValueError) as exc:
            # Deliberately not `{exc}`: the standard library embeds the rejected
            # input in its message ("invalid literal for int() ... 'hunter2'"),
            # and that value may be a secret.
            name = getattr(annotation, "__name__", str(annotation))
            msg = f"could not convert to {name}"
            raise CoercionError(msg) from exc

    if isinstance(annotation, type) and isinstance(value, annotation):
        return value
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value
    return value
