"""Format loaders, and the suffix registry that selects between them.

A loader turns the text of one file into flat canonical key paths carrying
origins. Where the underlying parser reports positions -- YAML, ``.properties``,
``.env`` -- the origin gets an exact line and column. Where it does not --
``tomllib``, ``json`` and ``ElementTree`` expose nothing -- the origin is
file-level, and the README says so rather than implying more.

Third parties add formats through the ``whence.formats`` entry-point group.
"""

from collections.abc import Callable, Mapping

from ..errors import FormatError
from ..keys import KeyPath
from ..origin import Tracked
from . import json as _json
from . import properties as _properties
from . import toml as _toml
from . import xml as _xml
from .yaml import load_yaml

__all__ = ["BUILTIN", "Loader", "loader_for", "registry", "suffixes"]

type Loader = Callable[[str, str], dict[KeyPath, Tracked]]
"""Takes ``(text, locator)`` and returns flat key paths to tracked values."""

BUILTIN: Mapping[str, Loader] = {
    "toml": _toml.load_toml,
    "json": _json.load_json,
    "yaml": load_yaml,
    "yml": load_yaml,
    "properties": _properties.load_properties,
    "xml": _xml.load_xml,
}
"""Suffix (without the dot) to loader, for every format whence ships."""


def registry(extra: Mapping[str, Loader] | None = None) -> dict[str, Loader]:
    """Build a suffix-to-loader table.

    Args:
        extra: Additional or overriding loaders, keyed by suffix without a dot.

    Returns:
        The built-in table updated with ``extra``.
    """
    table = dict(BUILTIN)
    if extra:
        table.update({_table_key(suffix): fn for suffix, fn in extra.items()})
    return table


def _table_key(suffix: str) -> str:
    """Normalise a suffix to a table key: lower case, no leading dot."""
    return suffix.lower().lstrip(".")


def suffixes(table: Mapping[str, Loader] | None = None) -> tuple[str, ...]:
    """List the suffixes a table can load.

    Args:
        table: A loader table; defaults to the built-ins.

    Returns:
        The suffixes, without dots.
    """
    return tuple(BUILTIN if table is None else table)


def loader_for(suffix: str, table: Mapping[str, Loader] | None = None) -> Loader:
    """Look up the loader for a file suffix.

    Args:
        suffix: With or without a leading dot, any case.
        table: A loader table; defaults to the built-ins.

    Returns:
        The loader.

    Raises:
        FormatError: If no loader handles the suffix.
    """
    known = BUILTIN if table is None else table
    key = _table_key(suffix)
    try:
        return known[key]
    except KeyError:
        msg = f"no loader for {suffix!r}; known formats: {', '.join(sorted(known))}"
        raise FormatError(msg) from None
