"""Binding to frozen dataclasses, on the standard library alone.

This is what keeps whence's zero-dependency claim honest: a project that does
not want pydantic still gets typed, validated configuration. When pydantic *is*
installed the other binder takes over automatically and you get its error
quality for free.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any, get_type_hints

from ..keys import KeyPath, join
from ..origin import RelativePath, Tracked
from .coerce import CoercionError, coerce, is_optional

__all__ = ["DataclassBinder", "declared_paths"]


def _is_dataclass_type(annotation: Any) -> bool:
    """Report whether an annotation is a nested dataclass to recurse into."""
    return isinstance(annotation, type) and dataclasses.is_dataclass(annotation)


def declared_paths(target: type, prefix: KeyPath = ()) -> set[KeyPath]:
    """List every key path a dataclass declares, recursing into nested ones.

    Args:
        target: The dataclass.
        prefix: The path it sits at.

    Returns:
        Every declared key path.
    """
    hints = get_type_hints(target)
    out: set[KeyPath] = set()
    for field in dataclasses.fields(target):
        annotation = hints.get(field.name, Any)
        path = (*prefix, field.name)
        if _is_dataclass_type(annotation):
            out |= declared_paths(annotation, path)
        else:
            out.add(path)
    return out


class DataclassBinder:
    """Binds a subtree of configuration onto a dataclass."""

    def supports(self, target: type) -> bool:
        """Report whether this binder handles the target.

        Args:
            target: The schema class.

        Returns:
            True for any dataclass.
        """
        return dataclasses.is_dataclass(target)

    def declared(self, target: type, prefix: KeyPath = ()) -> set[KeyPath]:
        """List the key paths the target declares.

        Args:
            target: The dataclass.
            prefix: The path it sits at.

        Returns:
            Every declared key path.
        """
        return declared_paths(target, prefix)

    def bind(
        self,
        target: type,
        values: Mapping[KeyPath, Tracked],
        prefix: KeyPath = (),
        problems: list[Any] | None = None,
    ) -> Any:
        """Construct the target from the resolved values.

        Args:
            target: The dataclass.
            values: Flat resolved values.
            prefix: The subtree to read.
            problems: A list to append problems to. Errors are accumulated
                rather than raised, so one run reports every mistake in the file
                instead of the first.

        Returns:
            An instance of ``target``, or ``None`` if construction failed.
        """
        from . import Problem

        collected = problems if problems is not None else []
        # Scoped to this target: a sibling's problem recorded earlier in the
        # shared list must not suppress an unrelated construction.
        before = len(collected)
        hints = get_type_hints(target)
        kwargs: dict[str, Any] = {}
        for field in dataclasses.fields(target):
            if not field.init:
                continue
            annotation = hints.get(field.name, Any)
            path = (*prefix, field.name)
            if _is_dataclass_type(annotation):
                nested = self.bind(annotation, values, path, collected)
                if nested is not None:
                    kwargs[field.name] = nested
                continue
            tracked = values.get(path)
            if tracked is None:
                if _has_default(field):
                    continue
                if is_optional(annotation):
                    kwargs[field.name] = None
                    continue
                collected.append(
                    Problem(join(path), None, None, "required, but nothing supplies it")
                )
                continue
            try:
                if annotation is RelativePath:
                    kwargs[field.name] = RelativePath.resolve_against(tracked.value, tracked.origin)
                    continue
                kwargs[field.name] = coerce(tracked.value, annotation)
            except (CoercionError, ValueError, TypeError) as exc:
                collected.append(Problem(join(path), tracked.value, tracked.origin, str(exc)))
        if len(collected) > before:
            # Constructing anyway would raise TypeError for the very field
            # already reported, and append a second `<root>` problem saying so.
            return None
        try:
            return target(**kwargs)
        except TypeError as exc:  # pragma: no cover - defensive
            collected.append(Problem(join(prefix) or "<root>", None, None, str(exc)))
            return None


def _has_default(field: "dataclasses.Field[Any]") -> bool:
    """Report whether a dataclass field can be left unset."""
    return (
        field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING
    )
