"""In-memory sources: programmatic overrides and schema defaults."""

from collections.abc import Mapping

from ..keys import KeyPath, canonical, flatten
from ..origin import Origin, Tracked
from ..tree import Layer

__all__ = ["MappingSource"]


class MappingSource:
    """A layer built from a Python mapping.

    Used for the two ends of the chain -- explicit overrides at the top and
    schema defaults at the bottom. Defaults are a source rather than a property
    of the target object on purpose: it means ``get``, ``dump`` and ``explain``
    all agree about what the configuration says. Spring's equivalent split,
    where ``@Value`` cannot see a bean's own defaults, is a genuine bug.
    """

    def __init__(
        self,
        data: Mapping[str, object] | Mapping[KeyPath, object],
        *,
        name: str = "overrides",
        locator: str | None = None,
    ) -> None:
        """Build a source from a flat or nested mapping.

        Args:
            data: Either nested (``{"db": {"host": "h"}}``) or already flat
                (``{("db", "host"): "h"}``).
            name: The layer name, used verbatim by ``explain``.
            locator: What origins should report; defaults to ``<name>``.
        """
        self.name = name
        self._origin = Origin(name, locator or f"<{name}>")
        if all(isinstance(key, tuple) for key in data):
            flat: dict[KeyPath, object] = {canonical(key): value for key, value in data.items()}
        else:
            flat = flatten({str(k): v for k, v in data.items()})
        self._entries = {path: Tracked(value, self._origin) for path, value in flat.items()}

    def load(self) -> Layer:
        """Return the mapping as a layer.

        Returns:
            The layer, marked found when the mapping was non-empty.
        """
        return Layer(self.name, self._entries, found=bool(self._entries))
