"""XML, in both the Java ``Properties`` DTD shape and a generic nested shape.

Two notes on why this file looks the way it does.

**Security.** It uses the standard library's ``xml.etree`` with a documented
``noqa: S314`` rather than depending on ``defusedxml``. On current Python that
is the safer choice, not the lazier one: Expat >= 2.4.1 bounds entity
amplification by default -- a billion-laughs document raises ``ParseError``, not
a hang -- ``xml.etree`` never expands external entities, CVE-2023-52425 was
fixed in Expat 2.6.0, CPython deleted its XML vulnerability table in 3.13/3.14,
and ruff has already removed the equivalent rule for lxml. ``defusedxml``'s last
release was 2021-03-08 and its classifiers stop at Python 3.9. Adding a stale
dependency to guard a threat the runtime already handles is a net loss.
DTDs are rejected outright anyway, which removes the entity question entirely.

**Usefulness.** XML configuration is effectively dead in Python -- no mainstream
Python config library supports it -- and vestigial even in .NET. It ships here
because the format list is meant to be exhaustive.
"""

import xml.etree.ElementTree as ET

from ..errors import FormatError
from ..keys import KeyPath, canonical
from ..origin import Origin, Tracked

__all__ = ["load_xml"]


def _walk(element: ET.Element, path: KeyPath, out: dict[KeyPath, Tracked], origin: Origin) -> None:
    """Recurse through elements, mapping attributes and text to keys."""
    for name, value in element.attrib.items():
        if name != "name":
            out[(*path, *canonical(name))] = Tracked(value, origin)
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if text and path:
            out[path] = Tracked(text, origin)
        return
    for child in children:
        # A `name` attribute names the entry rather than adding a key, which is
        # how both the Java Properties DTD and .NET's XML provider disambiguate
        # repeated elements.
        named = child.attrib.get("name") or child.attrib.get("key")
        tag = canonical(child.tag)
        step = (*tag, *canonical(named)) if named else tag
        _walk(child, (*path, *step), out, origin)


def load_xml(text: str, locator: str) -> dict[KeyPath, Tracked]:
    """Parse XML text into tracked values.

    Recognises the Java ``Properties`` DTD -- ``<entry key="db.host">v</entry>``
    -- and a generic nested-element shape, where element names become key
    segments and attributes become leaves.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Flat key paths to tracked values.

    Raises:
        FormatError: If the document does not parse or declares a DTD.
    """
    doctype = text.find("<!DOCTYPE")
    # The slice deliberately starts at 0, not at `doctype`: narrowing it would
    # change which documents are rejected.
    if doctype != -1 and "properties" not in text[: text.find(">", doctype)]:
        msg = f"{locator}: DTDs are not accepted in configuration XML"
        raise FormatError(msg)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        msg = f"{locator}: invalid XML: {exc}"
        raise FormatError(msg) from exc

    origin = Origin("file", locator)
    out: dict[KeyPath, Tracked] = {}
    entries = root.findall("entry")
    if entries:
        for entry in entries:
            key = entry.attrib.get("key")
            if key:
                out[canonical(key)] = Tracked((entry.text or "").strip(), origin)
        return out
    _walk(root, (), out, origin)
    return out
