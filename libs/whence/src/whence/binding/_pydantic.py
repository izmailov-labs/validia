"""Binding to pydantic models, when pydantic is installed.

whence never imports pydantic at module scope and never declares it as a
dependency. The binder is selected only when ``pydantic`` is importable, which
is what lets the same library serve a zero-dependency project and a
pydantic-native one.

The interesting work is the last step: pydantic reports errors with a ``loc``
tuple, and whence holds a map from key path to origin keyed identically. Joining
them at the key is what produces an error that names the file and line, which no
Python library does today.
"""

import functools
from collections.abc import Mapping
from importlib.util import find_spec
from typing import Any

from ..keys import KeyPath, join
from ..origin import Tracked
from ..tree import unflatten

__all__ = ["PydanticBinder", "pydantic_available"]


@functools.cache
def pydantic_available() -> bool:
    """Report whether pydantic can be imported.

    Returns:
        True when the package is installed.
    """
    return find_spec("pydantic") is not None


class PydanticBinder:
    """Binds a subtree of configuration onto a pydantic model."""

    def supports(self, target: type) -> bool:
        """Report whether this binder handles the target.

        Args:
            target: The schema class.

        Returns:
            True for a ``pydantic.BaseModel`` subclass.
        """
        if not pydantic_available():
            return False
        import pydantic

        return issubclass(target, pydantic.BaseModel)

    def declared(self, target: type, prefix: KeyPath = ()) -> set[KeyPath]:
        """List the key paths the model declares, recursing into sub-models.

        Args:
            target: The model.
            prefix: The path it sits at.

        Returns:
            Every declared key path.
        """
        import pydantic

        out: set[KeyPath] = set()
        for name, info in target.model_fields.items():  # type: ignore[attr-defined]
            annotation = info.annotation
            path = (*prefix, name)
            if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
                out |= self.declared(annotation, path)
            else:
                out.add(path)
        return out

    def bind(
        self,
        target: type,
        values: Mapping[KeyPath, Tracked],
        prefix: KeyPath = (),
        problems: list[Any] | None = None,
    ) -> Any:
        """Validate the subtree into a model instance.

        Args:
            target: The model.
            values: Flat resolved values.
            prefix: The subtree to read.
            problems: A list to append problems to.

        Returns:
            A model instance, or ``None`` when validation failed.
        """
        import pydantic

        from . import Problem

        collected = problems if problems is not None else []
        depth = len(prefix)
        subtree = {
            path[depth:]: tracked.value
            for path, tracked in values.items()
            if path[:depth] == prefix and len(path) > depth
        }
        try:
            return target.model_validate(unflatten(subtree))  # type: ignore[attr-defined]
        except pydantic.ValidationError as exc:
            for error in exc.errors():
                loc = tuple(str(part) for part in error["loc"])
                path = (*prefix, *loc)
                tracked = values.get(path)
                collected.append(
                    Problem(
                        join(path),
                        None if tracked is None else tracked.value,
                        None if tracked is None else tracked.origin,
                        error["msg"],
                    )
                )
            return None
