"""Asking questions on the terminal: the primitives, and the questions behind a suite.

Questions, menus and hints go to stderr, so a command's stdout stays clean data, and
answers come from ``input()``, which is what a test replaces. Every question re-asks
until its answer is usable, saying why, so a mistake costs one line rather than a
whole session; ending input or pressing Ctrl-C raises :class:`CancelledByUserError`, and the
commands write nothing until the last question is answered.
"""

import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..prompts.building import Question, prompt_questions, render_prompt, review_answer
from ..prompts.template import PromptTemplate
from ..suites.api import CaseSpec
from ..suites.expect import NO_TOOL, Reply, ToolCall
from ..suites.scaffold import folder_problem, name_problem
from ..suites.suite import ANSWER_TYPES, AnswerType, Grade, SuiteError, Tool, load_tools

__all__ = [
    "ANSWER_OPTIONS",
    "CancelledByUserError",
    "Interview",
    "folder_problem",
    "loose",
    "name_problem",
    "object_problem",
    "regex_problem",
    "value_problem",
]

Check = Callable[[str], str | None]
"""Validates an answer: ``None`` when it is usable, else why it is not."""

ANSWER_OPTIONS: tuple[tuple[AnswerType, str], ...] = (
    ("label", "a category from a fixed list"),
    ("json", "a JSON object"),
    ("text", "free text"),
    ("tool", "a call to one of its tools"),
)
"""The answer types, as the menu that asks for one shows them."""

NO_NULL = "TOML has no null; write a word such as unknown instead"


class CancelledByUserError(Exception):
    """The user ended input or interrupted instead of answering."""

    def __init__(self) -> None:
        """Say what happened, and that nothing was written."""
        super().__init__("cancelled; nothing was written")


def _say(text: str = "") -> None:
    """Show a line to the person answering."""
    print(text, file=sys.stderr, flush=True)


def _anything(_: str) -> str | None:
    """Accept every answer."""
    return None


# ------------------------------------------------------------- validators


def loose(text: str) -> Any:
    """Read a typed value: JSON when it parses (``2``, ``true``, ``"x"``), else the text itself.

    Args:
        text: The answer.

    Returns:
        The value.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _has_null(value: Any) -> bool:
    """Report whether a value holds a JSON null anywhere, which TOML cannot write."""
    if isinstance(value, dict):
        return any(_has_null(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_null(item) for item in value)
    return value is None


def value_problem(text: str) -> str | None:
    """Explain why a typed value cannot be written, if it cannot; empty is fine.

    Args:
        text: The answer.

    Returns:
        The problem, or ``None``.
    """
    return NO_NULL if text and _has_null(loose(text)) else None


def object_problem(text: str) -> str | None:
    """Explain why an answer is not a usable JSON object, if it is not.

    Args:
        text: The answer.

    Returns:
        The problem, or ``None``.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"not JSON: {exc.msg}"
    if not isinstance(value, dict):
        return 'expected a JSON object, as in {"category": "bug"}'
    return NO_NULL if _has_null(value) else None


def regex_problem(text: str) -> str | None:
    """Explain why a pattern would not work as a check, if it would not; empty skips.

    Args:
        text: The answer.

    Returns:
        The problem, or ``None``.
    """
    if not text:
        return None
    try:
        pattern = re.compile(text)
    except re.error as exc:
        return f"not a valid regular expression: {exc}"
    if pattern.search("") is not None:
        return "an empty reply matches it too, so it would check nothing"
    return None


def _needed(text: str) -> str | None:
    """Refuse an empty answer."""
    return None if text else "an answer is needed"


def _split(text: str) -> tuple[str, ...]:
    """Split a comma-separated answer, dropping blanks and repeats, keeping order."""
    return tuple(dict.fromkeys(part.strip() for part in text.split(",") if part.strip()))


def _free_id(taken: set[str]) -> str:
    """Suggest the next unused ``case-N`` id."""
    number = len(taken) + 1
    while f"case-{number}" in taken:
        number += 1
    return f"case-{number}"


class Interview:
    """Questions on the terminal, re-asked until each answer is usable."""

    # ------------------------------------------------------------ primitives

    def ask(self, question: str, default: str = "", valid: Check = _anything) -> str:
        """Ask one question; an empty answer takes the default.

        Args:
            question: What to ask.
            default: The answer Enter gives, shown in brackets when there is one.
            valid: Checks an answer; a problem is shown and the question asked again.

        Returns:
            The answer, stripped.

        Raises:
            CancelledByUserError: If input ends or is interrupted.
        """
        while True:
            shown = f" [{default}]" if default else ""
            print(f"{question}{shown}: ", end="", file=sys.stderr, flush=True)
            try:
                answer = input().strip() or default
            except (EOFError, KeyboardInterrupt):
                _say()
                raise CancelledByUserError from None
            problem = valid(answer)
            if problem is None:
                return answer
            _say(f"  {problem}")

    def choose(
        self,
        question: str,
        options: Sequence[tuple[str, str]],
        default: str | None = None,
        *,
        finish: str | None = None,
    ) -> str:
        """Offer numbered options; the answer may be a number or an option's name.

        Args:
            question: What is being chosen.
            options: ``(name, meaning)`` pairs, in the order to number them.
            default: The option Enter picks, if any.
            finish: When given, an empty answer means "none of these" and returns
                ``""``; this says how, as in ``"empty to finish"``.

        Returns:
            The chosen option's name, or ``""`` for a finish.
        """
        names = [name for name, _ in options]
        width = max(len(name) for name in names)
        _say(question)
        for number, (name, meaning) in enumerate(options, 1):
            _say(f"  {number}  {name:<{width}}  {meaning}".rstrip())

        def valid(answer: str) -> str | None:
            if answer in names or (answer.isdigit() and 1 <= int(answer) <= len(names)):
                return None
            if finish is not None and not answer:
                return None
            return f"choose 1-{len(names)}, or a name: {', '.join(names)}"

        prompt = f"Choose 1-{len(names)}" + (f" ({finish})" if finish else "")
        start = "" if default is None else str(names.index(default) + 1)
        answer = self.ask(prompt, start, valid)
        if answer.isdigit() and answer not in names:
            return names[int(answer) - 1]
        return answer

    def menu(self, question: str, items: Sequence[str], default: int = 1) -> int:
        """Offer numbered items too long to type back, such as whole instructions.

        Args:
            question: What is being chosen.
            items: The items; continuation lines are indented under their number.
            default: The number Enter picks.

        Returns:
            The chosen item's number, from 1.
        """
        _say(question)
        for number, item in enumerate(items, 1):
            first, *rest = item.splitlines() or [""]
            _say(f"  {number}  {first}")
            for line in rest:
                _say(f"     {line}")
        answer = self.ask(
            f"Choose 1-{len(items)}",
            str(default),
            lambda text: (
                None
                if text.isdigit() and 1 <= int(text) <= len(items)
                else f"choose a number from 1 to {len(items)}"
            ),
        )
        return int(answer)

    def confirm(self, question: str, *, default: bool = True) -> bool:
        """Ask a yes-or-no question.

        Args:
            question: What to ask.
            default: What Enter means.

        Returns:
            The answer.
        """
        answer = self.ask(
            f"{question} [{'Y/n' if default else 'y/N'}]",
            valid=lambda text: (
                None if text.lower() in ("", "y", "yes", "n", "no") else "answer y or n"
            ),
        )
        return default if not answer else answer.lower() in ("y", "yes")

    def many(self, question: str, valid: Check = _anything) -> list[str]:
        """Ask the same question until the answer is empty, collecting every answer.

        Args:
            question: What to ask, each time.
            valid: Checks each non-empty answer.

        Returns:
            The answers, in order.
        """
        answers: list[str] = []
        while answer := self.ask(question, valid=lambda text: valid(text) if text else None):
            answers.append(answer)
        return answers

    def block(self, intro: str) -> str:
        """Read several lines, up to one holding only a dot.

        Blank lines are kept, which is why a dot ends the text rather than an empty
        line: a prompt is usually more than one paragraph.

        Args:
            intro: What to type.

        Returns:
            The text, with a trailing newline.

        Raises:
            CancelledByUserError: If input ends before the closing dot.
        """
        _say(f"{intro}; end with a line holding only a dot.")
        lines: list[str] = []
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                _say()
                raise CancelledByUserError from None
            if line.strip() == ".":
                break
            lines.append(line.rstrip())
        return "\n".join(lines).strip("\n") + "\n"

    # -------------------------------------------------------- a suite's setup

    def answer_type(self) -> AnswerType:
        """Ask what the prompt answers with.

        Returns:
            The answer type.
        """
        chosen = self.choose("What does the prompt answer with?", ANSWER_OPTIONS, "label")
        return next(answer for answer in ANSWER_TYPES if answer == chosen)

    def labels(self) -> tuple[str, ...]:
        """Ask for a ``label`` suite's categories.

        Returns:
            At least two labels.
        """
        answer = self.ask(
            "Labels a reply may be, comma-separated",
            valid=lambda text: (
                None
                if len(_split(text)) >= 2
                else "list at least two: one label cannot tell a right answer from a wrong one"
            ),
        )
        return _split(answer)

    def required(self) -> tuple[str, ...]:
        """Ask for the keys every reply of a ``json`` suite must have.

        Returns:
            The keys, possibly none.
        """
        return _split(self.ask("Keys every reply must have, comma-separated (empty for none)"))

    def tools(self) -> tuple[Tool, ...]:
        """Ask for a ``tool`` suite's tools, one at a time.

        Returns:
            At least one tool, every argument a required string.
        """
        _say("Define the tools the model is offered.")
        tools: list[Tool] = []

        def valid(text: str) -> str | None:
            if not text:
                return None if tools else "a tool suite needs at least one tool"
            if text == NO_TOOL:
                return f"{NO_TOOL!r} means no call at all; choose another name"
            if any(tool.name == text for tool in tools):
                return f"{text!r} is already defined"
            return name_problem(text)

        while True:
            name = self.ask("Tool name" + (" (empty to finish)" if tools else ""), valid=valid)
            if not name:
                return tuple(tools)
            description = self.ask("What it does, as the model will read it")
            arguments = _split(self.ask("Its arguments, comma-separated (empty for none)"))
            parameters = {
                "type": "object",
                "properties": {argument: {"type": "string"} for argument in arguments},
                "required": list(arguments),
            }
            tools.append(Tool(name, parameters, description))

    def tools_file(self, cwd: Path) -> Path:
        """Ask for an existing tools file, checking it as it is named.

        Args:
            cwd: What a relative path is relative to.

        Returns:
            The file.
        """

        def valid(text: str) -> str | None:
            try:
                load_tools(cwd / text)
            except SuiteError as exc:
                return str(exc).replace("\n", "\n  ")
            except OSError as exc:
                return str(exc)
            return None

        return cwd / self.ask("Path to the tools file (a JSON array)", valid=valid)

    # ----------------------------------------------------------- the prompt

    def checked(self, template: PromptTemplate, question: str, text: str, *, required: bool) -> str:
        """Ask for one answer and run the template's core checks on it.

        A hint never blocks: it says what to change, and the answer is asked for
        again unless the user keeps it as it is.

        Args:
            template: Where the hints come from.
            question: The id of the question being answered.
            text: What to ask.
            required: Whether an empty answer is refused.

        Returns:
            The answer, possibly empty when not required.
        """
        needed = "this part of the prompt needs an answer"
        while True:
            answer = self.ask(text, valid=lambda reply: needed if required and not reply else None)
            hints = review_answer(template, question, answer)
            if not hints:
                return answer
            for hint in hints:
                _say(f"  hint {hint.id}: {hint.message}")
            if self.confirm("Keep it as it is?", default=False):
                return answer

    def answer(self, question: Question, template: PromptTemplate) -> str | list[str]:
        """Ask one prompt question the way a terminal can.

        A choice of short options is a named menu; one of long options -- whole
        instructions -- is numbered only. ``custom`` adds "write my own", and an
        optional choice adds "leave it out".

        Args:
            question: The question, as :func:`validia.prompts.building.prompt_questions` lists it.
            template: Where the core checks come from.

        Returns:
            The answer: text, or a list of texts for a ``many`` question.
        """
        if question.example:
            _say(f"  e.g. {question.example}")
        if question.kind == "many":
            answers: list[str] = []
            required = question.required
            while reply := self.checked(template, question.id, question.text, required=required):
                answers.append(reply)
                required = False
            return answers
        if question.kind == "text":
            return self.checked(template, question.id, question.text, required=question.required)
        values = [option.value for option in question.options]
        if any("\n" in value or len(value) > 60 for value in values):
            extra = ["write my own"] if question.custom else []
            picked = self.menu(question.text, [*values, *extra])
            if picked <= len(values):
                return values[picked - 1]
            return self.checked(template, question.id, "Your own", required=True)
        options = [(option.value, option.label) for option in question.options]
        if question.custom:
            options.append(("own", "write my own"))
        if not question.required:
            options.append(("skip", "leave it out"))
        picked_name = self.choose(question.text, options, values[0] if values else None)
        if picked_name == "skip":
            return ""
        if picked_name == "own":
            return self.checked(template, question.id, "Your own", required=True)
        return picked_name

    def build_prompt(self, template: PromptTemplate, grade: Grade, tools: Sequence[Tool]) -> str:
        """Build a prompt question by question, then show it before it is used.

        The questions and the rendering are :mod:`validia.prompts.building`'s, shared with
        every other front end; this method only asks.

        Args:
            template: The sections, hints and closing instructions.
            grade: The suite's grading, which picks the sections and the closing.
            tools: The suite's tools, listed in a tool prompt's closing.

        Returns:
            The prompt text.
        """
        while True:
            _say(f"Building the prompt from {template.source}.")
            answers = {
                question.id: self.answer(question, template)
                for question in prompt_questions(template, grade, tools)
            }
            text = render_prompt(template, grade, answers, tools)
            _say("The prompt:")
            for line in text.splitlines():
                _say(f"  | {line}".rstrip())
            if self.confirm("Use this prompt?"):
                return text

    # ------------------------------------------------------------- one case

    def case(
        self,
        grade: Grade,
        tools: Sequence[Tool],
        taken: set[str],
        groups: Sequence[str],
    ) -> CaseSpec:
        """Ask for one case in the shape a suite's answer type calls for.

        Args:
            grade: The suite's grading.
            tools: Its tools, for a ``tool`` suite.
            taken: Case ids already in use.
            groups: Existing first tags, to show as a hint.

        Returns:
            The case, ready for :func:`validia.suites.api.add_case` or a new suite.
        """
        case_id = self.ask(
            "Case id",
            _free_id(taken),
            lambda text: (
                name_problem(text)
                or (f"{text!r} is already a case here" if text in taken else None)
            ),
        )
        text = self.ask(
            "Input, the message the model gets",
            valid=lambda answer: None if answer else "a case needs an input",
        )
        hint = f" ({', '.join(groups)})" if groups else ""
        tags = _split(self.ask(f"Tags, comma-separated; the first groups results{hint}"))
        return CaseSpec(case_id, text, self.expected(grade, tools), tags)

    def expected(self, grade: Grade, tools: Sequence[Tool]) -> Any:
        """Ask for a case's expected answer.

        Args:
            grade: The suite's grading.
            tools: Its tools, for a ``tool`` suite.

        Returns:
            The value for ``expected``, in the answer type's shape.
        """
        if grade.type == "label":
            return self.choose("Expected label", [(label, "") for label in grade.labels])
        if grade.type == "json":
            return self._fields(grade.required)
        if grade.type == "tool":
            return self._call(tools)
        return self._checks()

    def _fields(self, required: tuple[str, ...]) -> dict[str, Any]:
        """Ask one question per required key, or for a whole object when there are none."""
        if not required:
            answer = self.ask(
                'Expected fields, as a JSON object like {"category": "bug"}', valid=object_problem
            )
            fields: dict[str, Any] = json.loads(answer)
            return fields
        _say("  one value per field: JSON (2, true) or plain text; empty skips it")
        values: dict[str, Any] = {}
        for key in required:
            answer = self.ask(key, valid=value_problem)
            if answer:
                values[key] = loose(answer)
        return values

    def _checks(self) -> dict[str, Any]:
        """Ask for text checks until there is at least one."""
        while True:
            found = {
                "contains": self.many("Must contain (one phrase per answer; empty to move on)"),
                "not_contains": self.many(
                    "Must not contain (one phrase per answer; empty to move on)"
                ),
                "matches": self.ask(
                    "Must match this regular expression (empty to skip)", valid=regex_problem
                ),
                "equals": self.ask("Must equal exactly (empty to skip)"),
            }
            checks = {key: value for key, value in found.items() if value}
            if checks:
                return checks
            _say("  a text case needs at least one check")

    def _call(self, tools: Sequence[Tool]) -> dict[str, Any]:
        """Ask for the tool a case expects, then each of its arguments."""
        options = [(tool.name, tool.description) for tool in tools]
        name = self.choose("Tool it should call", [*options, (NO_TOOL, "no call at all")])
        if name == NO_TOOL:
            return {"tool": NO_TOOL}
        properties = next(tool for tool in tools if tool.name == name).parameters.get("properties")
        args: dict[str, Any] = {}
        if isinstance(properties, dict) and properties:
            _say("  one value per argument: JSON or plain text; empty means any value")
            for key, schema in properties.items():
                answer = self.ask(f"{name}.{key}", valid=value_problem)
                if answer:
                    # The schema decides when it can: "5" for a string argument is
                    # the text "5", not the number a model's correct call never sends.
                    as_text = isinstance(schema, dict) and schema.get("type") == "string"
                    args[key] = answer if as_text else loose(answer)
        return {"tool": name, "args": args} if args else {"tool": name}

    # ------------------------------------------------------------ one reply

    def reply(self, grade: Grade, tools: Sequence[Tool], *, finish: str) -> Reply | None:
        """Ask for a reply to grade, in the shape the answer type produces.

        Args:
            grade: The suite's grading.
            tools: Its tools, for a ``tool`` suite.
            finish: How to stop, as in ``"empty to finish"``.

        Returns:
            The reply, or ``None`` when the user stopped.
        """
        if grade.type != "tool":
            text = self.ask(f"Reply ({finish})")
            return Reply(text=text) if text else None
        options = [(tool.name, tool.description) for tool in tools]
        name = self.choose(
            "Tool the reply called", [*options, (NO_TOOL, "answered without a tool")], finish=finish
        )
        if not name:
            return None
        if name == NO_TOOL:
            return Reply(text="answered without a tool")
        args = json.loads(self.ask("Its arguments, as a JSON object", "{}", object_problem))
        return Reply(tool_calls=(ToolCall(name, args),))
