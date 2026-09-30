"""YAML via PyYAML, with exact line and column numbers.

PyYAML's ordinary ``safe_load`` throws positions away. Composing the node tree
instead and reading ``node.start_mark`` keeps them, which is what lets a YAML
value report ``app.yaml:4:9``. That costs one recursive walk and no dependency
beyond PyYAML itself -- ``ruamel.yaml`` is not needed for this.

PyYAML is optional. It is the only format whence cannot load on the standard
library alone, so it lives behind the ``whence[yaml]`` extra and is imported
inside the loader, never at module scope.
"""

from typing import Any

from ..errors import FormatError
from ..keys import KeyPath, canonical
from ..origin import Origin, Tracked

__all__ = ["load_yaml"]


def _walk(loader: Any, node: Any, path: KeyPath, out: dict[KeyPath, Tracked], locator: str) -> None:
    """Recurse into mapping nodes, recording a position for every leaf."""
    if getattr(node, "id", "") == "mapping" and node.value:
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            _walk(loader, value_node, (*path, *canonical(str(key))), out, locator)
        return
    if not path:
        return
    out[path] = Tracked(
        loader.construct_object(node, deep=True),
        Origin("file", locator, node.start_mark.line + 1, node.start_mark.column + 1),
    )


def load_yaml(text: str, locator: str) -> dict[KeyPath, Tracked]:
    """Parse YAML text into tracked values carrying exact positions.

    Args:
        text: The file contents.
        locator: The path to report in origins.

    Returns:
        Flat key paths to tracked values.

    Raises:
        FormatError: If PyYAML is not installed, or the document does not parse
            or is not a mapping.
    """
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised by a stub test
        msg = f"{locator}: YAML support needs PyYAML: pip install 'whence[yaml]'"
        raise FormatError(msg) from exc

    loader = yaml.SafeLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except yaml.YAMLError as exc:
            msg = f"{locator}: invalid YAML: {exc}"
            raise FormatError(msg) from exc
        if node is None:
            return {}
        if getattr(node, "id", "") != "mapping":
            msg = f"{locator}: top level of a YAML config must be a mapping"
            raise FormatError(msg)
        out: dict[KeyPath, Tracked] = {}
        _walk(loader, node, (), out, locator)
        return out
    finally:
        loader.dispose()
