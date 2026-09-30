"""The ordered, named chain of sources.

Precedence is position in a list whose entries have names, not an integer
ordinal. Both models exist in the wild -- Spring uses a name-addressable list,
SmallRye uses integer ordinals -- and the list wins for the primary model: a
third-party source can say "immediately above the ``.env`` file" without knowing
what everything else chose, and two sources can never tie.

Ordinals remain available as sugar through :meth:`SourceChain.insert_by_ordinal`,
because letting an operator drop in a file that outranks the environment without
touching code is genuinely useful. They are converted to a position on insert,
so the ambiguity stays at the edge.
"""

from collections.abc import Iterator, Sequence

from .errors import ConfigError
from .sources import Source
from .tree import Resolved, resolve

__all__ = ["SourceChain"]


class SourceChain:
    """A mutable, ordered collection of named sources, highest precedence first."""

    def __init__(self, sources: Sequence[Source] = ()) -> None:
        """Build a chain.

        Args:
            sources: Sources in precedence order, highest first.
        """
        self._sources: list[Source] = list(sources)

    def __iter__(self) -> Iterator[Source]:
        """Iterate sources highest precedence first."""
        return iter(self._sources)

    def __len__(self) -> int:
        """Return the number of sources."""
        return len(self._sources)

    def __contains__(self, name: object) -> bool:
        """Report whether a source with this name is present."""
        return any(self._name(s) == name for s in self._sources)

    @staticmethod
    def _name(source: Source) -> str:
        """Read a source's name."""
        return str(getattr(source, "name", type(source).__name__))

    def names(self) -> tuple[str, ...]:
        """List source names, highest precedence first.

        Returns:
            The names.
        """
        return tuple(self._name(s) for s in self._sources)

    def _index(self, name: str) -> int:
        """Find a source by name.

        Raises:
            ConfigError: If no source carries that name.
        """
        for i, source in enumerate(self._sources):
            if self._name(source) == name:
                return i
        msg = f"no source named {name!r}; the chain holds {', '.join(self.names()) or '<nothing>'}"
        raise ConfigError(msg)

    def add_first(self, source: Source) -> "SourceChain":
        """Insert a source at the highest precedence.

        Args:
            source: The source.

        Returns:
            This chain, for chaining.
        """
        self._sources.insert(0, source)
        return self

    def add_last(self, source: Source) -> "SourceChain":
        """Append a source at the lowest precedence.

        Args:
            source: The source.

        Returns:
            This chain, for chaining.
        """
        self._sources.append(source)
        return self

    def add_before(self, relative: str, source: Source) -> "SourceChain":
        """Insert a source immediately above a named one.

        Args:
            relative: The name to insert above.
            source: The source.

        Returns:
            This chain, for chaining.
        """
        self._sources.insert(self._index(relative), source)
        return self

    def add_after(self, relative: str, source: Source) -> "SourceChain":
        """Insert a source immediately below a named one.

        Args:
            relative: The name to insert below.
            source: The source.

        Returns:
            This chain, for chaining.
        """
        self._sources.insert(self._index(relative) + 1, source)
        return self

    def replace(self, name: str, source: Source) -> Source:
        """Swap a source in place, keeping its precedence.

        This is how a source is decorated rather than displaced -- wrapping the
        environment source to decrypt values, for instance.

        Args:
            name: The source to replace.
            source: The replacement.

        Returns:
            The source that was removed.
        """
        i = self._index(name)
        previous = self._sources[i]
        self._sources[i] = source
        return previous

    def remove(self, name: str) -> Source:
        """Drop a source by name.

        Args:
            name: The source to remove.

        Returns:
            The source that was removed.
        """
        return self._sources.pop(self._index(name))

    def insert_by_ordinal(self, source: Source, ordinal: int) -> "SourceChain":
        """Insert a source by integer rank, higher winning.

        Args:
            source: The source, which must carry an ``ordinal`` attribute for
                comparison against existing entries.
            ordinal: The rank.

        Returns:
            This chain, for chaining.
        """
        for i, existing in enumerate(self._sources):
            if int(getattr(existing, "ordinal", 0)) < ordinal:
                self._sources.insert(i, source)
                return self
        self._sources.append(source)
        return self

    def load(self) -> Resolved:
        """Load every source and merge the layers.

        Returns:
            The winning values, the shadow chain, and every layer consulted.
        """
        layers = [source.load() for source in self._sources]
        return resolve(layers)
