"""validia's operations, for any front end: the console, a REST service, a notebook.

Every function here takes and returns plain data, and none of it reads a terminal
or prints. A REST handler builds a spec from a JSON body with
:meth:`SuiteSpec.from_dict`, calls an operation, and answers with
:func:`dataclasses.asdict` of the result or :func:`describe_suite`; a failure is a
:class:`SpecError` whose ``problems`` list is the body of a 422. The ``validia``
command is one front end over exactly these functions: it asks its questions in
:mod:`validia.cli.interview` and then calls them.

Building a prompt from questions is :mod:`validia.prompts.building`, re-exported here.
"""

import difflib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..prompts.building import (
    CLOSING,
    BuildError,
    Option,
    Question,
    closing_options,
    prompt_questions,
    render_prompt,
    review_answer,
    review_answers,
)
from .authoring import append_case, case_toml, suite_toml, write_suite
from .expect import NO_TOOL, Reply, ToolCall, Verdict
from .scaffold import folder_problem, name_problem
from .suite import (
    ANSWER_TYPES,
    AnswerType,
    Case,
    Grade,
    Suite,
    SuiteError,
    Tool,
    load_suite,
    load_tools,
)

__all__ = [
    "CLOSING",
    "BuildError",
    "CaseSpec",
    "Option",
    "Question",
    "SpecError",
    "SuiteSpec",
    "add_case",
    "check_reply",
    "closing_options",
    "create_suite",
    "describe_suite",
    "find_case",
    "locate_suite",
    "parse_reply",
    "prompt_questions",
    "render_prompt",
    "review_answer",
    "review_answers",
]

_CASE_FIELDS = ("id", "input", "expected", "tags")
_SUITE_FIELDS = (
    "name",
    "answer",
    "cases",
    "folder",
    "labels",
    "required",
    "prompt",
    "prompt_file",
    "tools",
    "tools_file",
    "description",
)
_TOOL_FIELDS = ("name", "description", "parameters")


class SpecError(ValueError):
    """A request cannot be carried out; ``problems`` lists every reason.

    Attributes:
        problems: One line per problem, as in ``cases[1].id: 'a' is used twice``.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        """Keep the problems, and say them all in the message."""
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _fields(data: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> list[str]:
    """Name every key the spec does not define, with the closest one it does."""
    problems: list[str] = []
    for key in data:
        if key not in allowed:
            close = difflib.get_close_matches(key, allowed, n=1)
            hint = f" - did you mean {close[0]!r}?" if close else ""
            problems.append(f"{where}{key}: no such field{hint}")
    return problems


def _text(data: Mapping[str, Any], key: str, where: str, problems: list[str]) -> str:
    """Read an optional string field."""
    value = data.get(key, "")
    if not isinstance(value, str):
        problems.append(f"{where}{key}: expected text")
        return ""
    return value


def _texts(data: Mapping[str, Any], key: str, where: str, problems: list[str]) -> tuple[str, ...]:
    """Read an optional list of strings."""
    value = data.get(key, [])
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        problems.append(f"{where}{key}: expected a list of text")
        return ()
    return tuple(value)


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """A case to add: one input, and what a right answer looks like.

    Attributes:
        id: Unique within the suite; letters, digits, ``.``, ``_`` and ``-``.
        input: The message the model gets.
        expected: In the suite's answer type's shape: a label; a dict of JSON
            fields; a dict of text checks; or ``{"tool": ..., "args": {...}}``.
        tags: Ordered labels; the first is the group results are reported by.
    """

    id: str
    input: str
    expected: Any
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], where: str = "") -> "CaseSpec":
        """Build a case from a JSON object, as a request body carries it.

        Args:
            data: The object.
            where: A prefix for problem locations, such as ``cases[2].``.

        Returns:
            The case.

        Raises:
            SpecError: If a field is unknown, missing or of the wrong type.
        """
        problems = _fields(data, _CASE_FIELDS, where)
        problems += [
            f"{where}{key}: missing" for key in ("id", "input", "expected") if key not in data
        ]
        case = cls(
            id=_text(data, "id", where, problems),
            input=_text(data, "input", where, problems),
            expected=data.get("expected"),
            tags=_texts(data, "tags", where, problems),
        )
        if problems:
            raise SpecError(problems)
        return case

    def problems(self, taken: Collection[str] = (), where: str = "") -> list[str]:
        """Check what can be checked without the suite file.

        The expected answer's shape is checked against the suite itself when the
        case is written, by the same loader that reads every suite.

        Args:
            taken: Ids already in use.
            where: A prefix for problem locations.

        Returns:
            Every problem found.
        """
        found: list[str] = []
        if problem := name_problem(self.id):
            found.append(f"{where}id: {problem}")
        elif self.id in taken:
            found.append(f"{where}id: {self.id!r} is already a case")
        if not self.input.strip():
            found.append(f"{where}input: a case needs an input")
        if self.expected is None:
            found.append(f"{where}expected: missing")
        return found


def _tool(data: Any, where: str, problems: list[str]) -> Tool | None:
    """Read one tool definition from a JSON object."""
    if not isinstance(data, Mapping):
        problems.append(f"{where[:-1]}: expected an object")
        return None
    problems += _fields(data, _TOOL_FIELDS, where)
    parameters = data.get("parameters", {"type": "object", "properties": {}})
    if not isinstance(parameters, dict):
        problems.append(f"{where}parameters: expected a JSON Schema object")
        parameters = {}
    return Tool(
        _text(data, "name", where, problems),
        parameters,
        _text(data, "description", where, problems),
    )


@dataclass(frozen=True, slots=True)
class SuiteSpec:
    """A suite to create: where it goes, how it is graded, its prompt and its cases.

    Attributes:
        name: The suite's folder name.
        answer: What the prompt answers with: ``label``, ``json``, ``text``, ``tool``.
        cases: At least one case.
        folder: The folder suites live in, relative to the project.
        labels: For ``label``: at least two.
        required: For ``json``: keys every reply must have.
        prompt: The prompt text, written to ``prompt.md``; or use ``prompt_file``.
        prompt_file: An existing prompt, relative to the project, that the suite
            points at instead of copying.
        tools: For ``tool``: the tools, written to ``tools.json``; or ``tools_file``.
        tools_file: An existing tools file, relative to the project.
        description: What the suite measures.
    """

    name: str
    answer: AnswerType
    cases: tuple[CaseSpec, ...]
    folder: str = "evals"
    labels: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    prompt: str = ""
    prompt_file: str = ""
    tools: tuple[Tool, ...] = ()
    tools_file: str = ""
    description: str = ""

    @property
    def grade(self) -> Grade:
        """The grading this spec describes."""
        return Grade(self.answer, labels=self.labels, required=self.required)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SuiteSpec":
        """Build a spec from a JSON object, as a request body carries it.

        Args:
            data: The object.

        Returns:
            The spec, its fields typed but not yet checked against each other;
            :func:`create_suite` does that.

        Raises:
            SpecError: If a field is unknown, missing or of the wrong type,
                listing every problem at once.
        """
        problems = _fields(data, _SUITE_FIELDS, "")
        problems += [f"{key}: missing" for key in ("name", "answer", "cases") if key not in data]
        answer = data.get("answer", "label")
        if answer not in ANSWER_TYPES:
            problems.append(f"answer: {answer!r} is not one of {list(ANSWER_TYPES)}")
            answer = "label"
        raw_cases = data.get("cases", [])
        cases: list[CaseSpec] = []
        if not isinstance(raw_cases, list):
            problems.append("cases: expected a list")
        else:
            for index, item in enumerate(raw_cases):
                if not isinstance(item, Mapping):
                    problems.append(f"cases[{index}]: expected an object")
                    continue
                try:
                    cases.append(CaseSpec.from_dict(item, f"cases[{index}]."))
                except SpecError as exc:
                    problems += exc.problems
        raw_tools = data.get("tools", [])
        tools: list[Tool] = []
        if not isinstance(raw_tools, list):
            problems.append("tools: expected a list")
        else:
            tools = [
                tool
                for index, item in enumerate(raw_tools)
                if (tool := _tool(item, f"tools[{index}].", problems)) is not None
            ]
        spec = cls(
            name=_text(data, "name", "", problems),
            answer=next(kind for kind in ANSWER_TYPES if kind == answer),
            cases=tuple(cases),
            folder=_text(data, "folder", "", problems) or "evals",
            labels=_texts(data, "labels", "", problems),
            required=_texts(data, "required", "", problems),
            prompt=_text(data, "prompt", "", problems),
            prompt_file=_text(data, "prompt_file", "", problems),
            tools=tuple(tools),
            tools_file=_text(data, "tools_file", "", problems),
            description=_text(data, "description", "", problems),
        )
        if problems:
            raise SpecError(problems)
        return spec

    def problems(self) -> list[str]:
        """Check the spec's parts against each other, without touching a disk.

        Returns:
            Every problem found.
        """
        found: list[str] = []
        if problem := name_problem(self.name):
            found.append(f"name: {problem}")
        if problem := folder_problem(self.folder):
            found.append(f"folder: {problem}")
        for key, value in (("prompt_file", self.prompt_file), ("tools_file", self.tools_file)):
            if value and (problem := folder_problem(value)):
                found.append(f"{key}: {problem.replace('a folder', 'a path')}")
        if self.answer == "label" and len(set(self.labels)) < 2:
            found.append("labels: a label suite needs at least two")
        if self.answer != "label" and self.labels:
            found.append("labels: only a label suite has labels")
        if self.answer != "json" and self.required:
            found.append("required: only a json suite has required keys")
        if self.answer == "tool" and bool(self.tools) == bool(self.tools_file):
            found.append("tools: a tool suite needs exactly one of tools and tools_file")
        if self.answer != "tool" and (self.tools or self.tools_file):
            found.append("tools: only a tool suite has tools")
        if bool(self.prompt.strip()) == bool(self.prompt_file):
            found.append("prompt: give exactly one of prompt and prompt_file")
        names = [tool.name for tool in self.tools]
        for index, tool in enumerate(self.tools):
            if tool.name == NO_TOOL:
                found.append(f"tools[{index}].name: {NO_TOOL!r} means no call at all")
            elif problem := name_problem(tool.name):
                found.append(f"tools[{index}].name: {problem}")
            elif names.count(tool.name) > 1 and names.index(tool.name) < index:
                found.append(f"tools[{index}].name: {tool.name!r} is defined twice")
        if not self.cases:
            found.append("cases: a suite needs at least one case")
        taken: set[str] = set()
        for index, case in enumerate(self.cases):
            found += case.problems(taken, f"cases[{index}].")
            taken.add(case.id)
        return found


def _inside(path: Path, root: Path) -> bool:
    """Report whether a path stays in the project once symlinks are followed.

    The lexical check on a spec catches ``..`` and absolute paths; this one catches
    a symlink inside the project that points out of it, which only the filesystem
    can tell.
    """
    return path.resolve().is_relative_to(root.resolve())


def locate_suite(root: Path, name: str, folder: str = "evals") -> Path:
    """Find a suite file by name, refusing any name that would leave the project.

    A front end that takes a suite's name from a URL or a form should come through
    here rather than joining paths itself: ``..`` in a name is a request for a file
    the API was never meant to touch.

    Args:
        root: The project.
        name: The suite's name.
        folder: The folder suites live in, relative to the project.

    Returns:
        The suite file's path.

    Raises:
        SpecError: If the name or folder is not a plain path inside the project,
            or there is no such suite.
    """
    problems = [f"name: {problem}" for problem in [name_problem(name)] if problem]
    problems += [f"folder: {problem}" for problem in [folder_problem(folder)] if problem]
    path = root / folder / name / "suite.toml"
    if not problems and not _inside(path, root):
        problems.append(f"name: {folder}/{name} leads outside the project")
    if not problems and not path.is_file():
        problems.append(f"name: no suite {folder}/{name}")
    if problems:
        raise SpecError(problems)
    return path


def _relative(path: Path, folder: Path) -> str:
    """Write a path the way a suite refers to it: relative to the suite's folder."""
    return path.resolve().relative_to(folder.resolve(), walk_up=True).as_posix()


def create_suite(spec: SuiteSpec, root: Path) -> Suite:
    """Create a suite: its file, its prompt and tools when given as text, all at once.

    Nothing is written unless the whole suite loads; a failure leaves no trace.

    Args:
        spec: What to create.
        root: The project the spec's folders and files are relative to.

    Returns:
        The suite, as loaded from what was written.

    Raises:
        SpecError: If the spec, a referenced file, or the written suite has a
            problem, listing every one.
    """
    problems = spec.problems()
    if problems:
        raise SpecError(problems)
    folder = root / spec.folder / spec.name
    if not _inside(folder, root):
        problems.append(f"folder: {spec.folder}/{spec.name} leads outside the project")
    elif folder.exists():
        problems.append(f"name: {spec.folder}/{spec.name} already exists")
    for key, value in (("prompt_file", spec.prompt_file), ("tools_file", spec.tools_file)):
        if value and not _inside(root / value, root):
            problems.append(f"{key}: {value!r} leads outside the project")
    if problems:
        raise SpecError(problems)
    prompt_file = root / spec.prompt_file if spec.prompt_file else None
    if prompt_file is not None and not prompt_file.is_file():
        problems.append(f"prompt_file: no such file {spec.prompt_file!r}")
    tools = spec.tools
    if spec.tools_file:
        try:
            tools = load_tools(root / spec.tools_file)
        except SuiteError as exc:
            problems += [f"tools_file: {problem}" for problem in exc.problems]
    if problems:
        raise SpecError(problems)

    files: dict[str, str] = {}
    prompt_ref = "prompt.md"
    if prompt_file is not None:
        prompt_ref = _relative(prompt_file, folder)
    else:
        files["prompt.md"] = spec.prompt if spec.prompt.endswith("\n") else f"{spec.prompt}\n"
    tools_ref = ""
    if spec.tools:
        definitions = [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in tools
        ]
        files["tools.json"] = json.dumps(definitions, indent=2) + "\n"
        tools_ref = "tools.json"
    elif spec.tools_file:
        tools_ref = _relative(root / spec.tools_file, folder)

    blocks: list[str] = []
    for index, case in enumerate(spec.cases):
        try:
            blocks.append(case_toml(case.id, case.input, case.expected, case.tags))
        except ValueError as exc:
            problems.append(f"cases[{index}].expected: {exc}")
    if problems:
        raise SpecError(problems)

    shown = PurePosixPath(spec.folder) / spec.name / "suite.toml"
    rendered = suite_toml(
        grade=spec.grade,
        prompt=prompt_ref,
        cases=blocks,
        run=shown.as_posix(),
        description=spec.description,
        tools=tools_ref,
    )
    try:
        return write_suite(folder, {"suite.toml": rendered, **files})
    except SuiteError as exc:
        raise SpecError(exc.problems) from exc


def add_case(suite_path: Path, case: CaseSpec) -> Suite:
    """Append one case to a suite, keeping it only if the suite still loads.

    Args:
        suite_path: The suite file.
        case: The case.

    Returns:
        The suite as it now stands.

    Raises:
        SpecError: If the case does not fit the suite, listing every problem.
    """
    suite = load_suite(suite_path)
    problems = case.problems({existing.id for existing in suite.cases})
    if problems:
        raise SpecError(problems)
    try:
        return append_case(suite_path, case_toml(case.id, case.input, case.expected, case.tags))
    except (SuiteError, ValueError) as exc:
        raise SpecError(getattr(exc, "problems", [str(exc)])) from exc


def parse_reply(data: Mapping[str, Any]) -> Reply:
    """Build a reply from a JSON object: ``{"text": ..., "tool_calls": [...]}``.

    Args:
        data: The object; each tool call is ``{"name": ..., "args": {...}}``.

    Returns:
        The reply.

    Raises:
        SpecError: If a field is unknown or of the wrong type.
    """
    problems = _fields(data, ("text", "tool_calls"), "")
    text = _text(data, "text", "", problems)
    calls: list[ToolCall] = []
    raw = data.get("tool_calls", [])
    if not isinstance(raw, list):
        problems.append("tool_calls: expected a list")
        raw = []
    for index, call in enumerate(raw):
        where = f"tool_calls[{index}]."
        if not isinstance(call, Mapping) or not isinstance(call.get("name"), str):
            problems.append(f"{where}name: expected text")
            continue
        problems += _fields(call, ("name", "args"), where)
        args = call.get("args", {})
        if not isinstance(args, dict):
            problems.append(f"{where}args: expected an object")
            args = {}
        calls.append(ToolCall(call["name"], args))
    if problems:
        raise SpecError(problems)
    return Reply(text=text, tool_calls=tuple(calls))


def find_case(suite: Suite, case_id: str) -> Case:
    """Look a case up by id.

    Args:
        suite: The suite.
        case_id: The case's id.

    Returns:
        The case.

    Raises:
        SpecError: If there is no such case, suggesting the closest one.
    """
    cases = {case.id: case for case in suite.cases}
    if case_id not in cases:
        close = difflib.get_close_matches(case_id, list(cases), n=1)
        hint = f"; did you mean {close[0]!r}?" if close else ""
        raise SpecError([f"no case {case_id!r} in {suite.path.name}{hint}"])
    return cases[case_id]


def check_reply(suite: Suite, case_id: str, reply: Reply) -> Verdict:
    """Grade one reply against one case, without calling a model.

    Args:
        suite: The suite.
        case_id: The case's id.
        reply: The reply to grade.

    Returns:
        The verdict.

    Raises:
        SpecError: If there is no such case, suggesting the closest one.
    """
    return find_case(suite, case_id).check(reply)


def describe_suite(suite: Suite, root: Path | None = None) -> dict[str, Any]:
    """Describe a suite as JSON-ready data: what a front end needs to show it.

    Args:
        suite: The suite.
        root: Paths are shown relative to this, when they are inside it.

    Returns:
        The answer type, labels, required keys, tools with their parameters, and
        every case's id, tags and input.
    """

    def shown(path: Path) -> str:
        if root is not None and path.resolve().is_relative_to(root.resolve()):
            return path.resolve().relative_to(root.resolve()).as_posix()
        return path.as_posix()

    return {
        "path": shown(suite.path),
        "prompt": shown(suite.prompt),
        "description": suite.description,
        "answer": suite.grade.type,
        "labels": list(suite.grade.labels),
        "required": list(suite.grade.required),
        "tools": [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in suite.tools
        ],
        "groups": suite.groups(),
        "cases": [{"id": c.id, "tags": list(c.tags), "input": c.input} for c in suite.cases],
    }
