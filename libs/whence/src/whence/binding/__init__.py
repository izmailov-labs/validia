"""Binding resolved configuration onto a typed schema.

Three behaviours here are not negotiable, and each one is a lesson from a
framework that got it wrong.

**Every error at once.** A configuration file with four mistakes should take one
run to fix, not four. environs added ``seal()`` for this; .NET's binder was
still silently swallowing enum failures as late as its own 8.0 breaking-change
note, which is headed "previously, the following code silently swallowed the
exceptions".

**Unknown keys are errors, by default.** A typo in a key name is the single most
common configuration bug, and the usual behaviour -- bind what matches, ignore
the rest -- makes it invisible. Spring ships a handler for this but does not
turn it on. whence turns it on and adds a suggestion.

**Nothing configured means ``None``.** An all-defaults object is
indistinguishable from a section that was never written, which is how a typo in
a section name goes unnoticed. Spring's ``BindResult`` makes the distinction and
so does this.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ..errors import BindError
from ..keys import KeyPath, canonical, join, suggest
from ..origin import Origin, Tracked
from ..secret import sanitize
from ._dataclasses import DataclassBinder
from ._pydantic import PydanticBinder, pydantic_available

__all__ = [
    "Binder",
    "DataclassBinder",
    "Problem",
    "PydanticBinder",
    "bind",
    "choose_binder",
    "pydantic_available",
    "render_problems",
]


_BINDERS: "tuple[Binder, ...]" = (PydanticBinder(), DataclassBinder())


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong with the configuration.

    Attributes:
        key: The dotted key.
        value: The offending value, redacted before rendering.
        origin: Where the value came from.
        reason: What is wrong with it.
        shadowed: Entries this value overrode, for context.
    """

    key: str
    value: Any
    origin: Origin | None
    reason: str
    shadowed: tuple[str, ...] = ()


def render_problems(problems: Sequence[Problem]) -> str:
    """Format problems the way whence reports them.

    The four-field ``Property / Value / Origin / Reason`` block plus an
    ``Action`` line is Spring Boot's failure-analysis layout, which is the best
    in the field and costs nothing to adopt.

    Args:
        problems: The problems to render.

    Returns:
        A multi-line message.
    """
    count = len(problems)
    lines = [f"{count} error{'s' if count != 1 else ''}", ""]
    for problem in problems:
        lines.append(f"  Property: {problem.key}")
        if problem.value is not None:
            lines.append(f"     Value: {sanitize(problem.key, problem.value)!r}")
        if problem.origin is not None:
            lines.append(f"    Origin: {problem.origin}")
        lines.append(f"    Reason: {problem.reason}")
        lines.extend(f"  Shadowed: {entry}" for entry in problem.shadowed)
        lines.append("")
    lines.append("Action: correct the configuration, or run `whence explain <key>`.")
    return "\n".join(lines)


class Binder(Protocol):
    """Turns a subtree of resolved configuration into a typed object."""

    def supports(self, target: type) -> bool:
        """Report whether this binder handles the target.

        Args:
            target: The schema class.

        Returns:
            True when it does.
        """
        ...

    def declared(self, target: type, prefix: KeyPath = ()) -> set[KeyPath]:
        """List the key paths the target declares.

        Args:
            target: The schema class.
            prefix: The path it sits at.

        Returns:
            Every declared key path.
        """
        ...

    def bind(
        self,
        target: type,
        values: Mapping[KeyPath, Tracked],
        prefix: KeyPath = (),
        problems: list[Any] | None = None,
    ) -> Any:
        """Construct the target.

        Args:
            target: The schema class.
            values: Flat resolved values.
            prefix: The subtree to read.
            problems: A list to append problems to.

        Returns:
            The instance, or ``None`` on failure.
        """
        ...


def choose_binder(target: type) -> Binder:
    """Pick the binder for a schema class.

    pydantic is preferred when the target is one of its models and the package
    is installed; otherwise the standard-library dataclass binder is used.

    Args:
        target: The schema class.

    Returns:
        A binder.

    Raises:
        BindError: If nothing can bind the target.
    """
    for binder in _BINDERS:
        if binder.supports(target):
            return binder
    name = getattr(target, "__name__", str(target))
    msg = (
        f"cannot bind to {name}: whence binds dataclasses, and pydantic models "
        "when pydantic is installed"
    )
    raise BindError(msg)


def bind(
    target: type,
    values: Mapping[KeyPath, Tracked],
    *,
    prefix: KeyPath = (),
    shadowed: Mapping[KeyPath, tuple[tuple[str, Tracked], ...]] | None = None,
    strict: bool = True,
) -> Any:
    """Bind resolved configuration onto a schema, reporting every problem.

    Args:
        target: The schema class.
        values: Flat resolved values.
        prefix: The subtree to bind.
        shadowed: Shadow records, used to enrich error messages.
        strict: Whether a key nothing declared is an error.

    Returns:
        An instance of ``target``.

    Raises:
        BindError: If anything failed, with every problem in one message.
    """
    binder = choose_binder(target)
    problems: list[Problem] = []
    instance = binder.bind(target, values, prefix, problems)

    if strict:
        declared = binder.declared(target, prefix)
        depth = len(prefix)
        for path, tracked in values.items():
            if path[:depth] != prefix or len(path) <= depth or path in declared:
                continue
            hint = suggest(path, declared)
            reason = "no such setting"
            if hint:
                reason = f"{reason} - did you mean {hint[0]!r}?"
            problems.append(Problem(join(path), tracked.value, tracked.origin, reason))

    if problems:
        if shadowed:
            problems = [replace(p, shadowed=_shadow_text(p.key, shadowed)) for p in problems]
        raise BindError(render_problems(problems))
    return instance


def _shadow_text(
    key: str,
    shadowed: Mapping[KeyPath, tuple[tuple[str, Tracked], ...]] | None,
) -> tuple[str, ...]:
    """Render the shadow chain for one key."""
    if not shadowed:
        return ()
    entries = shadowed.get(canonical(key), ())
    return tuple(f"{tracked.origin} = {sanitize(key, tracked.value)!r}" for _, tracked in entries)
