"""Prompt templates: the questions ``validia create`` asks to build a prompt, as data.

A template is a TOML file. Its ``[[sections]]`` are asked in order, one question each,
and each answer is written through the section's ``template``. Its ``[[hints]]`` are
the core checks: regular expressions run over an answer as it is typed -- shouting in
capitals, a rule with no reason, a word too vague to act on -- so a weak instruction
is caught while it is still one line. Its ``[output]`` table offers the closing "how
to answer" instruction for each answer type, filled in from the suite's labels, keys
or tools.

validia ships a default (:func:`default_template`); a ``prompt.toml`` in the project
replaces it, so a team's house style is a file they edit, not code they patch.
"""

import difflib
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from ..suites.suite import ANSWER_TYPES, AnswerType, Tool

__all__ = [
    "PROJECT_TEMPLATE",
    "Hint",
    "PromptTemplate",
    "Section",
    "TemplateError",
    "default_template",
    "default_text",
    "fill_output",
    "load_template",
]

PROJECT_TEMPLATE = "prompt.toml"
"""The project file that replaces the default template."""

_SECTION_FIELDS = (
    "id",
    "ask",
    "example",
    "template",
    "required",
    "many",
    "heading",
    "item",
    "choices",
    "answers",
)
_HINT_FIELDS = ("id", "message", "pattern", "missing", "sections")


class TemplateError(ValueError):
    """A prompt template is malformed; the message lists every problem in it."""


@dataclass(frozen=True, slots=True)
class Section:
    """One part of the prompt, and the question that fills it.

    Attributes:
        id: Names the section, for hints to refer to.
        ask: The question.
        example: An answer that shows the expected shape.
        template: How one answer is written; ``{answer}`` is what was typed.
        required: Whether an empty answer is refused, or skips the section.
        many: Ask again until the answer is empty, one item per answer.
        heading: For ``many``: the line written above the items.
        item: For ``many``: how each answer is written.
        choices: Answers to offer as a numbered menu, beside writing one's own.
        answers: The answer types the section applies to; empty means all.
    """

    id: str
    ask: str
    example: str = ""
    template: str = "{answer}"
    required: bool = True
    many: bool = False
    heading: str = ""
    item: str = "- {answer}"
    choices: tuple[str, ...] = ()
    answers: tuple[AnswerType, ...] = ()

    def render(self, answers: Sequence[str]) -> str:
        """Write this section's answers into prompt text.

        Args:
            answers: One answer, or every answer for a ``many`` section.

        Returns:
            The text, or ``""`` when there is nothing to write.
        """
        if not answers:
            return ""
        if self.many:
            lines = [self.item.replace("{answer}", answer) for answer in answers]
            return "\n".join([self.heading, *lines] if self.heading else lines)
        return self.template.replace("{answer}", answers[0])


@dataclass(frozen=True, slots=True)
class Hint:
    """A core check on an answer: a pattern that should, or should not, appear.

    Attributes:
        id: Names the hint, as it is shown.
        message: What to do about it.
        pattern: Fires when this regular expression matches.
        missing: Fires when this one does not.
        sections: The sections it reads; empty means all.
    """

    id: str
    message: str
    pattern: str = ""
    missing: str = ""
    sections: tuple[str, ...] = ()

    def fires(self, section: str, answer: str) -> bool:
        """Report whether this hint applies to an answer.

        Args:
            section: The id of the section the answer is for.
            answer: The answer.

        Returns:
            True when the hint should be shown.
        """
        if self.sections and section not in self.sections:
            return False
        if self.pattern:
            return re.search(self.pattern, answer) is not None
        return re.search(self.missing, answer) is None


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A loaded, checked template.

    Attributes:
        sections: The questions, in order.
        hints: The core checks run on each answer.
        output: For each answer type, the closing instructions to offer, first
            one the default.
        source: Where the template came from, for the user to see.
    """

    sections: tuple[Section, ...]
    hints: tuple[Hint, ...]
    output: dict[AnswerType, tuple[str, ...]]
    source: str

    def sections_for(self, answer: AnswerType) -> tuple[Section, ...]:
        """List the sections that apply to an answer type, in order.

        Args:
            answer: The suite's answer type.

        Returns:
            The sections.
        """
        return tuple(s for s in self.sections if not s.answers or answer in s.answers)

    def hints_for(self, section: str, answer: str) -> list[Hint]:
        """List the hints an answer sets off.

        Args:
            section: The id of the section the answer is for.
            answer: The answer.

        Returns:
            The hints that fire, in template order.
        """
        return [hint for hint in self.hints if hint.fires(section, answer)]


def _either(words: Sequence[str]) -> str:
    """Join words as a choice: ``a``, ``a or b``, ``a, b or c``."""
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} or {words[-1]}"


def fill_output(
    text: str, *, labels: Sequence[str], keys: Sequence[str], tools: Sequence[Tool]
) -> str:
    """Fill an output instruction's ``{labels}``, ``{keys}`` and ``{tools}``.

    Args:
        text: The instruction, from the template's ``[output]`` table.
        labels: The suite's labels.
        keys: The keys every JSON reply must have.
        tools: The suite's tools.

    Returns:
        The instruction, ready to write.
    """
    listed = "\n".join(
        f"- {tool.name}: {tool.description}" if tool.description else f"- {tool.name}"
        for tool in tools
    )
    return (
        text.replace("{labels}", _either(labels))
        .replace("{keys}", ", ".join(keys) or "the fields the task calls for")
        .replace("{tools}", listed)
    )


class _Reader:
    """Pulls typed fields out of parsed TOML, collecting problems instead of raising."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fields(self, table: dict[str, Any], where: str, allowed: tuple[str, ...]) -> None:
        for key in table:
            if key not in allowed:
                close = difflib.get_close_matches(key, allowed, n=1)
                hint = f" - did you mean {close[0]!r}?" if close else ""
                self.problems.append(f"{where}{key}: no such field{hint}")

    def text(self, table: dict[str, Any], key: str, where: str, *, required: bool = False) -> str:
        value = table.get(key)
        if value is None:
            if required:
                self.problems.append(f"{where}{key}: missing")
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            self.problems.append(f"{where}{key}: expected a non-empty string")
            return ""
        return value

    def flag(self, table: dict[str, Any], key: str, where: str, default: bool) -> bool:
        value = table.get(key, default)
        if not isinstance(value, bool):
            self.problems.append(f"{where}{key}: expected true or false")
            return default
        return value

    def texts(self, table: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            self.problems.append(f"{where}{key}: expected a list of non-empty strings")
            return ()
        return tuple(value)

    def regex(self, value: str, where: str) -> None:
        if not value:
            return
        try:
            re.compile(value)
        except re.error as exc:
            self.problems.append(f"{where}: not a valid regular expression: {exc}")


def _answer_types(reader: _Reader, table: dict[str, Any], where: str) -> tuple[AnswerType, ...]:
    """Read a section's ``answers`` list, checking each is an answer type."""
    found: list[AnswerType] = []
    for answer in reader.texts(table, "answers", where):
        known = next((kind for kind in ANSWER_TYPES if kind == answer), None)
        if known is None:
            reader.problems.append(f"{where}answers: {answer!r} is not an answer type")
        else:
            found.append(known)
    return tuple(found)


def _section(reader: _Reader, table: dict[str, Any], where: str) -> Section:
    """Read one ``[[sections]]`` table."""
    reader.fields(table, where, _SECTION_FIELDS)
    section = Section(
        id=reader.text(table, "id", where, required=True),
        ask=reader.text(table, "ask", where, required=True),
        example=reader.text(table, "example", where),
        template=reader.text(table, "template", where) or "{answer}",
        required=reader.flag(table, "required", where, default=True),
        many=reader.flag(table, "many", where, default=False),
        heading=reader.text(table, "heading", where),
        item=reader.text(table, "item", where) or "- {answer}",
        choices=reader.texts(table, "choices", where),
        answers=_answer_types(reader, table, where),
    )
    if section.many and section.choices:
        reader.problems.append(f"{where}choices: a `many` section asks freely; drop one of them")
    return section


def _hint(reader: _Reader, table: dict[str, Any], where: str, sections: set[str]) -> Hint:
    """Read one ``[[hints]]`` table; exactly one of ``pattern`` and ``missing``."""
    reader.fields(table, where, _HINT_FIELDS)
    hint = Hint(
        id=reader.text(table, "id", where, required=True),
        message=reader.text(table, "message", where, required=True),
        pattern=reader.text(table, "pattern", where),
        missing=reader.text(table, "missing", where),
        sections=reader.texts(table, "sections", where),
    )
    if bool(hint.pattern) == bool(hint.missing):
        reader.problems.append(f"{where[:-1]}: give exactly one of `pattern` and `missing`")
    reader.regex(hint.pattern, f"{where}pattern")
    reader.regex(hint.missing, f"{where}missing")
    reader.problems.extend(
        f"{where}sections: no section {name!r}" for name in hint.sections if name not in sections
    )
    return hint


def _output(reader: _Reader, data: dict[str, Any]) -> dict[AnswerType, tuple[str, ...]]:
    """Read the ``[output]`` table: one or more instructions per answer type."""
    table = data.get("output")
    if not isinstance(table, dict):
        reader.problems.append("output: missing; add an [output] table")
        return {}
    reader.fields(table, "output.", ANSWER_TYPES)
    output: dict[AnswerType, tuple[str, ...]] = {}
    for answer in ANSWER_TYPES:
        options = reader.texts(table, answer, "output.")
        if not options:
            reader.problems.append(f"output.{answer}: give at least one instruction")
        output[answer] = options
    return output


def _tables(reader: _Reader, data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Read an array of tables, which may be absent."""
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        reader.problems.append(f"{key}: expected [[{key}]] tables")
        return []
    return value


def _parse(text: str, source: str) -> PromptTemplate:
    """Check a template's text and build it, or raise every problem at once."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = f"{source} is not valid TOML: {exc}"
        raise TemplateError(msg) from None
    reader = _Reader()
    reader.fields(data, "", ("sections", "hints", "output"))
    sections = tuple(
        _section(reader, table, f"sections[{index}].")
        for index, table in enumerate(_tables(reader, data, "sections"))
    )
    if not sections:
        reader.problems.append("sections: give at least one [[sections]] table")
    ids = [section.id for section in sections]
    reader.problems.extend(
        f"sections: {name!r} is used twice" for name in sorted({i for i in ids if ids.count(i) > 1})
    )
    hints = tuple(
        _hint(reader, table, f"hints[{index}].", set(ids))
        for index, table in enumerate(_tables(reader, data, "hints"))
    )
    output = _output(reader, data)
    if reader.problems:
        count = len(reader.problems)
        lines = "\n".join(f"  {problem}" for problem in reader.problems)
        msg = f"{source} has {count} problem{'s' if count != 1 else ''}:\n{lines}"
        raise TemplateError(msg)
    return PromptTemplate(sections=sections, hints=hints, output=output, source=source)


def default_text() -> str:
    """Return the built-in template's text, comments and all, for copying.

    Returns:
        The TOML text.
    """
    return resources.files(__package__).joinpath("default.toml").read_text(encoding="utf-8")


def default_template() -> PromptTemplate:
    """Load the built-in template.

    Returns:
        The template.
    """
    return _parse(default_text(), "the built-in prompt template")


def load_template(path: Path) -> PromptTemplate:
    """Load a project's template.

    Args:
        path: The ``prompt.toml`` file.

    Returns:
        The template.

    Raises:
        TemplateError: If it is malformed, listing every problem.
        OSError: If it cannot be read.
    """
    return _parse(path.read_text(encoding="utf-8"), path.name)
