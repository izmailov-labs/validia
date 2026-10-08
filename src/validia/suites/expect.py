"""What a right answer looks like, one class per answer type, each able to check a reply.

Every check is code: deterministic, free, and the same twice. Each one forgives only
what the model is free to vary without changing the answer -- surrounding whitespace,
case, a code fence -- and nothing else. A failed check says why, so a transcript reads
``priority: expected 'high', got 'low'`` rather than a bare fail.

A :class:`Reply` carries both halves of what a model can answer with: text, and the
tools it asked to call. The runner builds one from franca's response (``.text`` and
``.tool_calls``); every check reads the half its answer type is about.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "NO_TOOL",
    "Expected",
    "Fields",
    "Label",
    "Reply",
    "TextChecks",
    "ToolCall",
    "ToolUse",
    "Verdict",
]

NO_TOOL = "none"
"""The tool name a case expects when the right answer is to call no tool at all."""

_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```\Z", re.DOTALL)
_JSON_TYPES = {list: "an array", str: "a string", int: "a number", float: "a number"}


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked to call.

    Attributes:
        name: The tool's name.
        args: Its arguments, decoded.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    """Everything the model answered with, for one case.

    Attributes:
        text: The text of the answer.
        tool_calls: Tools it asked to call, in the order it asked.
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def empty(self) -> bool:
        """Whether the model said nothing and called nothing."""
        return not self.text.strip() and not self.tool_calls


@dataclass(frozen=True, slots=True)
class Verdict:
    """The outcome of checking one reply.

    Attributes:
        passed: Whether the reply is right.
        reason: Why it is not, when it is not; empty on a pass.
    """

    passed: bool
    reason: str = ""


def _squash(text: str) -> str:
    """Collapse whitespace and case, for comparisons that should not hinge on either."""
    return " ".join(text.split()).casefold()


def _clip(text: str, limit: int = 60) -> str:
    """Shorten a reply for a failure reason; the transcript keeps all of it."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 3]}..."


def _same(got: Any, want: Any) -> bool:
    """Compare JSON values, without Python's ``True == 1`` leaking into a grade."""
    if isinstance(got, bool) or isinstance(want, bool):
        return got is want
    return bool(got == want)


def _mismatches(got: dict[str, Any], want: dict[str, Any], skip: tuple[str, ...] = ()) -> list[str]:
    """List every listed key that is missing or holds another value."""
    problems: list[str] = []
    for key, value in want.items():
        if key not in got:
            if key not in skip:
                problems.append(f"{key}: missing")
        elif not _same(got[key], value):
            problems.append(f"{key}: expected {value!r}, got {got[key]!r}")
    return problems


@dataclass(frozen=True, slots=True)
class Label:
    """The reply must be one category label.

    The comparison ignores case, surrounding whitespace, and quotes, emphasis or a
    full stop around the label, so ``**Urgent.**`` is ``urgent``. Anything more --
    ``urgent, because`` -- fails: the prompt asked for the label alone.

    Attributes:
        value: The right label.
    """

    value: str

    def check(self, reply: Reply) -> Verdict:
        """Check a reply.

        Args:
            reply: The model's answer.

        Returns:
            The verdict.
        """
        answer = _squash(reply.text).strip(" \"'`*.!")
        if answer == _squash(self.value):
            return Verdict(passed=True)
        return Verdict(passed=False, reason=f"expected {self.value!r}, got {_clip(reply.text)!r}")


@dataclass(frozen=True, slots=True)
class Fields:
    """The reply must be one JSON object, with these fields holding these values.

    A ```` ```json ```` fence around the object is allowed. Fields the case does not
    list may hold anything; listed ones must match exactly, strings included.

    Attributes:
        values: Fields whose values must match.
        required: Fields that must be present, whatever they hold.
    """

    values: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def check(self, reply: Reply) -> Verdict:
        """Check a reply.

        Args:
            reply: The model's answer.

        Returns:
            The verdict, naming every field that is missing or wrong.
        """
        text = reply.text.strip()
        fenced = _FENCE.match(text)
        if fenced:
            text = fenced.group(1)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return Verdict(passed=False, reason=f"not JSON: {exc.msg} at line {exc.lineno}")
        if not isinstance(data, dict):
            kind = _JSON_TYPES.get(type(data), "a literal")
            return Verdict(passed=False, reason=f"expected a JSON object, got {kind}")
        problems = [f"{key}: missing" for key in self.required if key not in data]
        problems += _mismatches(data, self.values, skip=self.required)
        return Verdict(passed=not problems, reason="; ".join(problems))


@dataclass(frozen=True, slots=True)
class TextChecks:
    """Checks on a free-text reply; every one given must hold.

    ``equals``, ``contains`` and ``not_contains`` ignore case and collapse
    whitespace. ``matches`` is a regular expression searched for anywhere in the
    reply, as written: add ``(?i)`` to it to ignore case.

    Attributes:
        equals: The whole reply, when there is exactly one right one.
        contains: Phrases the reply must include.
        not_contains: Phrases the reply must not include.
        matches: A pattern the reply must match.
    """

    equals: str = ""
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    matches: str = ""

    def check(self, reply: Reply) -> Verdict:
        """Check a reply.

        Args:
            reply: The model's answer.

        Returns:
            The verdict, naming every check that failed.
        """
        said = _squash(reply.text)
        problems: list[str] = []
        if self.equals and said != _squash(self.equals):
            problems.append(f"expected {self.equals!r}, got {_clip(reply.text)!r}")
        problems += [
            f"missing {phrase!r}" for phrase in self.contains if _squash(phrase) not in said
        ]
        problems += [f"says {phrase!r}" for phrase in self.not_contains if _squash(phrase) in said]
        if self.matches and re.search(self.matches, reply.text) is None:
            problems.append(f"does not match {self.matches!r}")
        return Verdict(passed=not problems, reason="; ".join(problems))


@dataclass(frozen=True, slots=True)
class ToolUse:
    """The reply must call this tool with these arguments, or call no tool at all.

    One matching call is enough when the model calls several. Arguments the case
    does not list may hold anything; listed ones must match exactly. A tool of
    :data:`NO_TOOL` means the right answer needs no tool: a greeting, a thank-you,
    a question the prompt says to answer directly. A set without those cases
    cannot tell a model that picks tools well from one that calls them always.

    Attributes:
        tool: The tool that must be called, or :data:`NO_TOOL`.
        args: Arguments whose values must match.
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)

    def check(self, reply: Reply) -> Verdict:
        """Check a reply.

        Args:
            reply: The model's answer.

        Returns:
            The verdict, naming the call made and every argument that is wrong.
        """
        called = [call.name for call in reply.tool_calls]
        if self.tool == NO_TOOL:
            if called:
                return Verdict(passed=False, reason=f"called {', '.join(called)}; expected no tool")
            return Verdict(passed=True)
        candidates = [call for call in reply.tool_calls if call.name == self.tool]
        if not candidates:
            got = ", ".join(called) if called else f"no tool, said {_clip(reply.text)!r}"
            return Verdict(passed=False, reason=f"expected a call to {self.tool}, got {got}")
        attempts = [_mismatches(call.args, self.args) for call in candidates]
        if any(not problems for problems in attempts):
            return Verdict(passed=True)
        return Verdict(passed=False, reason=f"{self.tool}: {'; '.join(attempts[0])}")


Expected = Label | Fields | TextChecks | ToolUse
"""What a case expects, in the shape its suite's answer type calls for."""
