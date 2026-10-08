"""Building a prompt from a template, with no terminal attached.

This is the part of prompt creation every front end shares -- the console, a REST
API, a notebook. The questions come out as data (:func:`prompt_questions`), each
answer can be checked on its own as it arrives (:func:`review_answer`), and a
complete set of answers becomes the prompt (:func:`render_prompt`). A front end
decides only how to ask: the console prints menus, a web form renders the same
:class:`Question` objects as fields, and both end in the same text.

Everything here is plain data: :func:`dataclasses.asdict` turns a question or a
hint into the dict a JSON response needs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ..suites.suite import Grade, Tool
from .template import Hint, PromptTemplate, fill_output

__all__ = [
    "CLOSING",
    "BuildError",
    "Option",
    "Question",
    "closing_options",
    "prompt_questions",
    "render_prompt",
    "review_answer",
    "review_answers",
]

CLOSING = "closing"
"""The id of the last question: how the model should answer."""

Answer = str | Sequence[str]
"""One question's answer: text, or a list of texts for a ``many`` question."""


class BuildError(ValueError):
    """The answers cannot make a prompt; ``problems`` lists every reason.

    Attributes:
        problems: One line per problem, as in ``role: needs an answer``.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        """Keep the problems, and say them all in the message."""
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True, slots=True)
class Option:
    """One answer a question offers.

    Attributes:
        value: The answer itself, as it is written into the prompt.
        label: A short description, when the value does not speak for itself.
    """

    value: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class Question:
    """One thing to ask, in a form any front end can show.

    Attributes:
        id: Where the answer goes: a section id, or :data:`CLOSING`.
        text: The question.
        kind: ``text`` takes one answer; ``many`` takes a list, one item per
            answer; ``choice`` offers :attr:`options`.
        required: Whether an empty answer is refused, or skips the part.
        example: An answer that shows the expected shape.
        options: For ``choice``: the answers on offer.
        custom: For ``choice``: whether an answer outside the options is taken.
    """

    id: str
    text: str
    kind: Literal["text", "many", "choice"]
    required: bool = True
    example: str = ""
    options: tuple[Option, ...] = ()
    custom: bool = False


def closing_options(template: PromptTemplate, grade: Grade, tools: Sequence[Tool]) -> list[str]:
    """List the closing instructions on offer, filled in for this suite.

    Args:
        template: Where the instructions come from.
        grade: The suite's grading: its answer type, labels and keys.
        tools: The suite's tools.

    Returns:
        The instructions, the template's default first.
    """
    return [
        fill_output(option, labels=grade.labels, keys=grade.required, tools=tools)
        for option in template.output[grade.type]
    ]


def prompt_questions(
    template: PromptTemplate, grade: Grade, tools: Sequence[Tool] = ()
) -> tuple[Question, ...]:
    """List the questions that build a prompt for this suite, in order.

    Args:
        template: The sections to ask about.
        grade: The suite's grading, which picks the sections and the closing.
        tools: The suite's tools, listed in a tool prompt's closing.

    Returns:
        One question per section that applies, then :data:`CLOSING`.
    """
    questions: list[Question] = []
    for section in template.sections_for(grade.type):
        if section.choices:
            options = tuple(Option(choice) for choice in section.choices)
            question = Question(
                section.id,
                section.ask,
                "choice",
                section.required,
                section.example,
                options,
                custom=True,
            )
        else:
            kind: Literal["many", "text"] = "many" if section.many else "text"
            question = Question(section.id, section.ask, kind, section.required, section.example)
        questions.append(question)
    closings = tuple(Option(text) for text in closing_options(template, grade, tools))
    questions.append(
        Question(CLOSING, "How should it answer?", "choice", options=closings, custom=True)
    )
    return tuple(questions)


def review_answer(template: PromptTemplate, question: str, answer: str) -> tuple[Hint, ...]:
    """Run the template's core checks on one answer.

    A hint is advice, never a refusal: the front end shows it and the person
    decides whether to rewrite.

    Args:
        template: Where the checks come from.
        question: The id of the question the answer is for.
        answer: The answer.

    Returns:
        The hints it sets off, in template order.
    """
    return tuple(template.hints_for(question, answer)) if answer else ()


def review_answers(
    template: PromptTemplate, answers: Mapping[str, Answer]
) -> dict[str, tuple[Hint, ...]]:
    """Run the core checks on a whole set of answers, as a form submits them.

    Args:
        template: Where the checks come from.
        answers: Question ids to answers.

    Returns:
        Question ids to the hints their answers set off; quiet ones left out.
    """
    found: dict[str, tuple[Hint, ...]] = {}
    for question, answer in answers.items():
        items = [answer] if isinstance(answer, str) else list(answer)
        hints = tuple(hint for item in items for hint in review_answer(template, question, item))
        if hints:
            found[question] = hints
    return found


def _items(question: Question, answer: object, problems: list[str]) -> list[str]:
    """Normalise one answer to a list of non-blank texts, noting a wrong shape."""
    if question.kind == "many":
        if isinstance(answer, str) or not isinstance(answer, list | tuple):
            problems.append(f"{question.id}: expected a list of answers")
            return []
        if not all(isinstance(item, str) for item in answer):
            problems.append(f"{question.id}: every answer must be text")
            return []
        return [item.strip() for item in answer if item.strip()]
    if not isinstance(answer, str):
        problems.append(f"{question.id}: expected text")
        return []
    offered = {option.value for option in question.options}
    if question.kind == "choice" and not question.custom and answer and answer not in offered:
        problems.append(f"{question.id}: {answer!r} is not one of the options")
    return [answer.strip()] if answer.strip() else []


def render_prompt(
    template: PromptTemplate,
    grade: Grade,
    answers: Mapping[str, Answer],
    tools: Sequence[Tool] = (),
) -> str:
    """Turn a complete set of answers into the prompt.

    Args:
        template: The sections the answers fill.
        grade: The suite's grading, which picks the sections that apply.
        answers: Question ids, as :func:`prompt_questions` lists them, to answers.
            A skipped optional question may be left out or answered with ``""``.
        tools: The suite's tools.

    Returns:
        The prompt, sections separated by blank lines, ending in a newline.

    Raises:
        BuildError: If an answer is missing, misshapen, or for no question,
            listing every problem at once.
    """
    questions = prompt_questions(template, grade, tools)
    known = {question.id for question in questions}
    problems = [f"{key}: no such question" for key in answers if key not in known]
    sections = {section.id: section for section in template.sections_for(grade.type)}
    parts: list[str] = []
    for question in questions:
        empty: Answer = [] if question.kind == "many" else ""
        items = _items(question, answers.get(question.id, empty), problems)
        if question.required and not items:
            problems.append(f"{question.id}: needs an answer")
        if question.id == CLOSING:
            parts.extend(items[:1])
        elif items:
            parts.append(sections[question.id].render(items))
    if problems:
        raise BuildError(problems)
    return "\n\n".join(part for part in parts if part) + "\n"
