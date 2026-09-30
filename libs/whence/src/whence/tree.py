"""Layers, the merge, and the shadow records that make `explain` possible.

whence merges a *flat* namespace of canonical key paths rather than nested
object trees. Environment variables and command-line arguments are inherently
flat, so any tree model has to flatten them anyway; doing it once at the edge
means one merge rule instead of two.

The cost of a flat model, everywhere it has been tried, is that lists overlay
element-wise: .NET's indexed keys leave source A's ``Modules:2`` alive beneath
source B's ``Modules:0,1``, a landmine its own documentation apologises for.
whence avoids it by treating a list as a leaf, so lists replace wholesale.

The other cost is that a merged value belongs to no single source and nobody can
explain it. That is exactly what :class:`Resolved` fixes: every losing entry is
kept, so the question "what did this override?" has an answer.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .keys import KeyPath, join
from .origin import Tracked

__all__ = ["Layer", "Resolved", "resolve", "unflatten"]


@dataclass(frozen=True, slots=True)
class Layer:
    """Everything one source contributed, under its own name.

    Attributes:
        name: The source's name in the chain, used verbatim by ``explain``.
        entries: Flat canonical key paths to tracked values.
        found: Whether the source produced anything. A source that was consulted
            and found nothing still appears in ``explain``, because absence is
            information.
    """

    name: str
    entries: Mapping[KeyPath, Tracked] = field(default_factory=dict)
    found: bool = True


@dataclass(frozen=True, slots=True)
class Resolved:
    """The outcome of merging layers, with the losers kept.

    Attributes:
        values: The winning value for every key.
        shadowed: Per key, the ``(layer name, value)`` pairs that lost, in
            precedence order.
        layers: Every layer consulted, highest precedence first, including the
            ones that contributed nothing.
    """

    values: Mapping[KeyPath, Tracked]
    shadowed: Mapping[KeyPath, tuple[tuple[str, Tracked], ...]]
    layers: tuple[Layer, ...]

    def keys(self) -> Iterable[KeyPath]:
        """Return every key that has a winning value.

        Returns:
            The resolved key paths.
        """
        return self.values.keys()

    def dotted(self) -> dict[str, Tracked]:
        """Return the resolved values keyed by dotted name.

        Returns:
            A mapping from ``"db.host"`` to its tracked value.
        """
        return {join(path): value for path, value in self.values.items()}


def resolve(layers: Sequence[Layer]) -> Resolved:
    """Merge layers, highest precedence first.

    Args:
        layers: Layers ordered so that index 0 wins.

    Returns:
        The winning values plus, for every contested key, the entries that lost.
    """
    values: dict[KeyPath, Tracked] = {}
    shadowed: dict[KeyPath, list[tuple[str, Tracked]]] = {}
    for layer in layers:
        for path, tracked in layer.entries.items():
            if path in values:
                shadowed.setdefault(path, []).append((layer.name, tracked))
            else:
                values[path] = tracked
    return Resolved(
        values=values,
        shadowed={path: tuple(losers) for path, losers in shadowed.items()},
        layers=tuple(layers),
    )


def unflatten(values: Mapping[KeyPath, object]) -> dict[str, object]:
    """Rebuild a nested mapping from flat key paths.

    Args:
        values: Flat canonical key paths to values.

    Returns:
        A nested dictionary. Where a key path would have to pass *through* a
        leaf, the leaf loses: the deeper, more specific key wins, since it can
        only have come from a source that named it explicitly.
    """
    out: dict[str, object] = {}
    for path in sorted(values, key=len):
        cursor = out
        for segment in path[:-1]:
            nxt = cursor.get(segment)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[segment] = nxt
            cursor = nxt
        cursor[path[-1]] = values[path]
    return out
