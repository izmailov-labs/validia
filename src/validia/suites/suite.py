"""Evaluation suites: the prompt under test, the cases to run it on, and how to grade them.

A suite is one TOML file. Its ``[grade]`` table names the answer type, and each
case's ``expected`` takes the shape that type calls for::

    prompt = "prompt.md"            # the system prompt under test, relative to this file

    [grade]
    type = "label"                  # label, json or text
    labels = ["urgent", "normal"]

    [[cases]]
    id = "outage-login"
    tags = ["urgent", "outage"]     # ordered; results are grouped by the first
    input = "Nobody on our team can log in since 9am."
    expected = "urgent"

=========  ==================================  =============================================
type       the reply is                        ``expected`` is
=========  ==================================  =============================================
``label``  one of ``labels``: a category       the right label, as in ``"urgent"``
``json``   a JSON object                       fields that must match, as in
                                               ``{ category = "billing" }``; ``required``
                                               in ``[grade]`` lists keys every reply needs
``text``   free text                           checks: any of ``equals``, ``contains``,
                                               ``not_contains`` and ``matches`` (a regex)
``tool``   a call to one of the tools in       the tool and the arguments that must match,
           the file ``tools`` names            as in ``{ tool = "lookup_order", args =
                                               { order_id = "48213" } }``, or
                                               ``{ tool = "none" }`` for no call at all
=========  ==================================  =============================================

A ``tools`` file is a JSON array of tool definitions -- ``name``, ``description``
and ``parameters`` (a JSON Schema), plus an optional ``strict`` -- the same fields
franca's ``ToolDef`` takes, so the runner hands them over unchanged.

The expected answers live in the suite and the prompt in a file of its own, so the
model under test only ever sees the prompt and each case's input. Loading checks the
whole file and reports every problem at once: a suite with four mistakes should take
one run to fix, not four.
"""

import difflib
import json
import re
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn, get_args

from .expect import NO_TOOL, Expected, Fields, Label, Reply, TextChecks, ToolUse, Verdict

__all__ = [
    "ANSWER_TYPES",
    "AnswerType",
    "Case",
    "Grade",
    "Suite",
    "SuiteError",
    "Tool",
    "load_suite",
    "load_tools",
]

AnswerType = Literal["label", "json", "text", "tool"]

ANSWER_TYPES: tuple[AnswerType, ...] = get_args(AnswerType)
"""Every answer type, in the order they are offered."""

UNTAGGED = "untagged"
"""The group a case without tags is reported under."""

_GRADE_FIELDS: dict[AnswerType, tuple[str, ...]] = {
    "label": ("type", "labels"),
    "json": ("type", "required"),
    "text": ("type",),
    "tool": ("type",),
}
_TEXT_CHECKS = ("equals", "contains", "not_contains", "matches")
_TOOL_FIELDS = ("name", "description", "parameters", "strict")


class SuiteError(ValueError):
    """A suite file is malformed; the message lists every problem in it.

    Attributes:
        problems: One line per problem, for a caller that reports them as data.
    """

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        """Keep the problems beside the rendered message."""
        super().__init__(message)
        self.problems = list(problems) or [message]


@dataclass(frozen=True, slots=True)
class Grade:
    """How each reply is scored.

    Attributes:
        type: The answer type, which decides the shape of every ``expected``.
        labels: For ``label``: the closed set of answers.
        required: For ``json``: keys every reply must have.
    """

    type: AnswerType
    labels: tuple[str, ...] = ()
    required: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool the model is offered, as franca's ``ToolDef`` takes it.

    Attributes:
        name: The tool's name, as the model will spell it in a call.
        parameters: The JSON Schema of its arguments.
        description: What it does, in the model's own context window.
        strict: Whether the provider must enforce the schema exactly.
    """

    name: str
    parameters: dict[str, Any]
    description: str = ""
    strict: bool | None = None


@dataclass(frozen=True, slots=True)
class Case:
    """One input to run the prompt on, and what a right answer looks like.

    Attributes:
        id: Unique within the suite; results and transcripts are keyed by it.
        input: The user message sent to the model.
        expected: What the reply is checked against.
        tags: Ordered labels; the first is the group results are reported by.
    """

    id: str
    input: str
    expected: Expected
    tags: tuple[str, ...] = ()

    def check(self, reply: Reply) -> Verdict:
        """Grade one reply to this case.

        An empty reply -- no text and no tool call -- always fails, whatever the
        answer type: no answer is not a wrong answer, and it must never score like
        a considered one.

        Args:
            reply: The model's answer.

        Returns:
            The verdict.
        """
        if reply.empty:
            return Verdict(passed=False, reason="empty reply")
        return self.expected.check(reply)


@dataclass(frozen=True, slots=True)
class Suite:
    """A loaded, validated suite.

    Attributes:
        path: The suite file.
        prompt: The prompt file, resolved against the suite's directory.
        grade: How replies are scored.
        cases: The cases, in file order.
        description: What the suite measures.
        tools: The tools the model is offered, if any.
        tools_file: The file they were read from.
    """

    path: Path
    prompt: Path
    grade: Grade
    cases: tuple[Case, ...]
    description: str = ""
    tools: tuple[Tool, ...] = ()
    tools_file: Path | None = None

    def summary(self) -> str:
        """Describe the grader in one line, as ``run --dry-run`` prints it.

        Returns:
            The answer type, and what it checks against.
        """
        grade = self.grade
        if grade.type == "label":
            return f"label  ({', '.join(grade.labels)})"
        if grade.type == "tool":
            return f"tool  ({', '.join(tool.name for tool in self.tools)})"
        if grade.type == "json" and grade.required:
            return f"json  (required: {', '.join(grade.required)})"
        return grade.type

    def groups(self) -> dict[str, int]:
        """Count cases by their first tag, the group results are reported by.

        Returns:
            Case counts, in order of first appearance.
        """
        return dict(Counter(case.tags[0] if case.tags else UNTAGGED for case in self.cases))


class _Reader:
    """Pulls typed fields out of parsed TOML, collecting problems instead of raising."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fields(self, table: dict[str, Any], where: str, allowed: tuple[str, ...]) -> None:
        """Report keys the format does not define, with a suggestion when one is close."""
        for key in table:
            if key not in allowed:
                close = difflib.get_close_matches(key, allowed, n=1)
                hint = f" - did you mean {close[0]!r}?" if close else ""
                self.problems.append(f"{where}{key}: no such field{hint}")

    def text(self, table: dict[str, Any], key: str, where: str, *, required: bool = True) -> str:
        """Read a non-empty string, or ``""`` when an optional one is absent."""
        value = table.get(key)
        if value is None:
            if required:
                self.problems.append(f"{where}{key}: missing")
            return ""
        if not isinstance(value, str) or not value.strip():
            self.problems.append(f"{where}{key}: expected a non-empty string")
            return ""
        return value

    def texts(self, table: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
        """Read a list of non-empty strings; absent means empty."""
        value = table.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            self.problems.append(f"{where}{key}: expected a list of non-empty strings")
            return ()
        return tuple(value)

    def tables(self, table: dict[str, Any], key: str) -> list[Any]:
        """Read a non-empty array of tables."""
        value = table.get(key)
        if not isinstance(value, list) or not value:
            self.problems.append(f"{key}: expected at least one [[{key}]] table")
            return []
        return value

    def table(self, table: dict[str, Any], key: str, where: str, example: str) -> dict[str, Any]:
        """Read a table, saying what one should look like when it is something else."""
        value = table.get(key)
        if value is None:
            self.problems.append(f"{where}{key}: missing")
            return {}
        if not isinstance(value, dict):
            self.problems.append(f"{where}{key}: expected a table, as in {example}")
            return {}
        return value


def _grade(reader: _Reader, data: dict[str, Any]) -> Grade | None:
    """Read the ``[grade]`` table; ``None`` when no answer type could be settled."""
    table = data.get("grade")
    if not isinstance(table, dict):
        reader.problems.append("grade: missing; add a [grade] table")
        return None
    kind = reader.text(table, "type", "grade.")
    if kind not in ANSWER_TYPES:
        if kind:
            reader.problems.append(
                f"grade.type: {kind!r} is not an answer type; expected one of {list(ANSWER_TYPES)}"
            )
        return None
    answer: AnswerType = kind
    reader.fields(table, "grade.", _GRADE_FIELDS[answer])
    labels = reader.texts(table, "labels", "grade.")
    required = reader.texts(table, "required", "grade.")
    if answer == "label" and not labels and "labels" in table:
        reader.problems.append("grade.labels: a label grader needs at least one label")
    elif answer == "label" and not labels:
        reader.problems.append("grade.labels: missing; list the labels a reply may be")
    return Grade(answer, labels=labels, required=required)


def _label(reader: _Reader, grade: Grade, table: dict[str, Any], where: str) -> Label:
    """Read a ``label`` case's expected answer, which must be one of the labels."""
    value = reader.text(table, "expected", where)
    if value and grade.labels and value not in grade.labels:
        reader.problems.append(
            f"{where}expected: {value!r} is not one of the labels {list(grade.labels)}"
        )
    return Label(value)


def _fields(reader: _Reader, grade: Grade, table: dict[str, Any], where: str) -> Fields:
    """Read a ``json`` case's expected fields; with no ``required`` keys, it must list some."""
    values = reader.table(table, "expected", where, '{ category = "billing" }')
    if isinstance(table.get("expected"), dict) and not values and not grade.required:
        reader.problems.append(
            f"{where}expected: checks nothing; list fields, or set grade.required"
        )
    return Fields(values=values, required=grade.required)


def _checks(reader: _Reader, table: dict[str, Any], where: str) -> TextChecks:
    """Read a ``text`` case's checks, refusing ones that would pass any reply."""
    found = reader.table(table, "expected", where, '{ contains = ["Export"] }')
    if isinstance(table.get("expected"), dict) and not any(key in found for key in _TEXT_CHECKS):
        reader.problems.append(f"{where}expected: no checks; use {', '.join(_TEXT_CHECKS)}")
    inner = f"{where}expected."
    reader.fields(found, inner, _TEXT_CHECKS)
    checks = TextChecks(
        equals=reader.text(found, "equals", inner, required=False),
        contains=reader.texts(found, "contains", inner),
        not_contains=reader.texts(found, "not_contains", inner),
        matches=reader.text(found, "matches", inner, required=False),
    )
    if checks.matches:
        try:
            pattern = re.compile(checks.matches)
        except re.error as exc:
            reader.problems.append(f"{inner}matches: not a valid regular expression: {exc}")
        else:
            if pattern.search("") is not None:
                reader.problems.append(
                    f"{inner}matches: an empty reply matches it too, so it checks nothing"
                )
    return checks


def _tools(reader: _Reader, file: Path, name: str) -> tuple[Tool, ...]:
    """Read and check a tools file; ``name`` is how problems refer to it."""
    if not file.is_file():
        reader.problems.append(f"tools: no such file {name!r} next to the suite")
        return ()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        reader.problems.append(f"tools: {name} is not valid JSON: {exc.msg} at line {exc.lineno}")
        return ()
    if not isinstance(data, list) or not data:
        reader.problems.append(f"tools: {name} must be a JSON array of tool definitions")
        return ()
    tools: list[Tool] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        where = f"{name}[{index}]."
        if not isinstance(entry, dict):
            reader.problems.append(f"{name}[{index}]: expected an object")
            continue
        reader.fields(entry, where, _TOOL_FIELDS)
        tool_name = reader.text(entry, "name", where)
        if tool_name == NO_TOOL:
            reader.problems.append(f'{where}name: {NO_TOOL!r} is reserved for "call no tool"')
        elif tool_name in seen:
            reader.problems.append(f"{where}name: {tool_name!r} is defined twice")
        seen.add(tool_name)
        parameters = entry.get("parameters")
        if not isinstance(parameters, dict):
            reader.problems.append(f"{where}parameters: expected a JSON Schema object")
            parameters = {}
        strict = entry.get("strict")
        if strict is not None and not isinstance(strict, bool):
            reader.problems.append(f"{where}strict: expected true or false")
            strict = None
        description = reader.text(entry, "description", where, required=False)
        tools.append(Tool(tool_name, parameters, description, strict))
    return tuple(tools)


def _tool_use(
    reader: _Reader, tools: tuple[Tool, ...], table: dict[str, Any], where: str
) -> ToolUse:
    """Read a ``tool`` case's expected call, against the tools the suite offers."""
    found = reader.table(table, "expected", where, '{ tool = "lookup_order" }')
    if not found:
        return ToolUse(NO_TOOL)
    inner = f"{where}expected."
    reader.fields(found, inner, ("tool", "args"))
    name = reader.text(found, "tool", inner)
    args = found.get("args", {})
    if not isinstance(args, dict):
        reader.problems.append(f'{inner}args: expected a table, as in {{ order_id = "48213" }}')
        args = {}
    offered = {tool.name: tool for tool in tools}
    if name == NO_TOOL:
        if args:
            reader.problems.append(f"{inner}args: a case that expects no tool has none to check")
    elif name and offered and name not in offered:
        close = difflib.get_close_matches(name, list(offered), n=1)
        hint = f" - did you mean {close[0]!r}?" if close else ""
        reader.problems.append(f"{inner}tool: {name!r} is not one of the tools{hint}")
    elif name in offered:
        known = offered[name].parameters.get("properties")
        if isinstance(known, dict):
            reader.fields(args, f"{inner}args.", tuple(known))
    return ToolUse(name, args)


def _expected(
    reader: _Reader,
    grade: Grade | None,
    tools: tuple[Tool, ...],
    table: dict[str, Any],
    where: str,
) -> Expected:
    """Read a case's ``expected`` in the shape its answer type calls for."""
    if grade is None:
        return TextChecks()
    if grade.type == "label":
        return _label(reader, grade, table, where)
    if grade.type == "json":
        return _fields(reader, grade, table, where)
    if grade.type == "tool":
        return _tool_use(reader, tools, table, where)
    return _checks(reader, table, where)


def _cases(
    reader: _Reader, data: dict[str, Any], grade: Grade | None, tools: tuple[Tool, ...]
) -> tuple[Case, ...]:
    """Read every ``[[cases]]`` table, checking ids are unique."""
    cases: list[Case] = []
    seen: dict[str, int] = {}
    for index, table in enumerate(reader.tables(data, "cases")):
        where = f"cases[{index}]."
        if not isinstance(table, dict):
            reader.problems.append(f"cases[{index}]: expected a table")
            continue
        reader.fields(table, where, ("id", "tags", "input", "expected"))
        case = Case(
            id=reader.text(table, "id", where),
            input=reader.text(table, "input", where),
            expected=_expected(reader, grade, tools, table, where),
            tags=reader.texts(table, "tags", where),
        )
        if case.id in seen:
            reader.problems.append(
                f"{where}id: {case.id!r} is already used by cases[{seen[case.id]}]"
            )
        elif case.id:
            seen[case.id] = index
        cases.append(case)
    return tuple(cases)


def _raise(path: Path, problems: list[str]) -> NoReturn:
    """Raise one :class:`SuiteError` listing every problem found in a file."""
    count = len(problems)
    lines = "\n".join(f"  {problem}" for problem in problems)
    msg = f"{path} has {count} problem{'s' if count != 1 else ''}:\n{lines}"
    raise SuiteError(msg, problems)


def load_tools(path: Path) -> tuple[Tool, ...]:
    """Load and check a tools file on its own, as ``validia create`` offers one.

    Args:
        path: The JSON file of tool definitions.

    Returns:
        The tools.

    Raises:
        SuiteError: If the file is missing or malformed, listing every problem.
    """
    reader = _Reader()
    tools = _tools(reader, path, path.name)
    if reader.problems:
        _raise(path, reader.problems)
    return tools


def load_suite(path: Path) -> Suite:
    """Load a suite file and check all of it.

    Args:
        path: The suite's TOML file.

    Returns:
        The suite, with its prompt path resolved against the suite's directory.

    Raises:
        SuiteError: If the file is not valid TOML or breaks the format, listing
            every problem at once.
        OSError: If the file cannot be read.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        msg = f"{path} is not valid TOML: {exc}"
        raise SuiteError(msg) from None

    reader = _Reader()
    reader.fields(data, "", ("description", "prompt", "tools", "grade", "cases"))
    description = reader.text(data, "description", "", required=False)
    prompt_name = reader.text(data, "prompt", "")
    prompt = path.parent / prompt_name
    if prompt_name and not prompt.is_file():
        reader.problems.append(f"prompt: no such file {prompt_name!r} next to the suite")
    grade = _grade(reader, data)
    tools_name = reader.text(data, "tools", "", required=grade is not None and grade.type == "tool")
    tools = _tools(reader, path.parent / tools_name, tools_name) if tools_name else ()
    cases = _cases(reader, data, grade, tools)

    if reader.problems or grade is None:
        _raise(path, reader.problems)
    return Suite(
        path=path,
        prompt=prompt,
        grade=grade,
        cases=cases,
        description=description,
        tools=tools,
        tools_file=path.parent / tools_name if tools_name else None,
    )
