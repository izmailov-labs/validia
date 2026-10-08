"""Prompt building without a terminal: questions as data, answers in, prompt out."""

from dataclasses import asdict

import pytest

from validia.prompts.building import (
    CLOSING,
    BuildError,
    Option,
    prompt_questions,
    render_prompt,
    review_answer,
    review_answers,
)
from validia.prompts.template import default_template
from validia.suites.suite import Grade, Tool

TEMPLATE = default_template()
LABEL = Grade("label", labels=("urgent", "normal"))


def test_questions_are_plain_data_a_form_can_render() -> None:
    questions = prompt_questions(TEMPLATE, LABEL)
    assert [q.id for q in questions] == ["role", "task", "context", "rules", CLOSING]
    assert [q.kind for q in questions] == ["text", "text", "text", "many", "choice"]
    assert [q.required for q in questions] == [True, True, False, False, True]
    closing = asdict(questions[-1])
    assert closing["options"] == (
        {"value": "Reply with one word, urgent or normal, and nothing else.", "label": ""},
    )
    assert closing["custom"] is True


def test_a_text_suite_adds_the_tone_choice() -> None:
    questions = prompt_questions(TEMPLATE, Grade("text"))
    tone = next(q for q in questions if q.id == "tone")
    assert tone.kind == "choice"
    assert tone.options[0] == Option("plain and direct")
    assert not tone.required


def test_a_tool_closing_lists_the_tools() -> None:
    tools = (Tool("lookup_order", {}, "Look up one order"),)
    closing = prompt_questions(TEMPLATE, Grade("tool"), tools)[-1]
    assert "- lookup_order: Look up one order" in closing.options[0].value


ANSWERS = {
    "role": "the support assistant for Acme Shop",
    "task": "sort tickets by how soon they need a person",
    "rules": ["judge by what is happening, because calm customers report outages too"],
    CLOSING: "Reply with one word, urgent or normal, and nothing else.",
}


def test_answers_render_the_same_prompt_as_the_console() -> None:
    assert render_prompt(TEMPLATE, LABEL, ANSWERS) == (
        "You are the support assistant for Acme Shop.\n\n"
        "Your job is to sort tickets by how soon they need a person.\n\n"
        "Keep to these rules:\n"
        "- judge by what is happening, because calm customers report outages too\n\n"
        "Reply with one word, urgent or normal, and nothing else.\n"
    )


def test_optional_answers_may_be_empty_or_left_out() -> None:
    text = render_prompt(TEMPLATE, LABEL, {**ANSWERS, "context": "", "rules": []})
    assert "Keep to these rules" not in text


def test_every_problem_with_the_answers_is_listed() -> None:
    answers = {"role": 3, "rules": "one rule", "colour": "red"}
    with pytest.raises(BuildError) as caught:
        render_prompt(TEMPLATE, LABEL, answers)  # type: ignore[arg-type]
    assert caught.value.problems == [
        "colour: no such question",
        "role: expected text",
        "role: needs an answer",
        "task: needs an answer",
        "rules: expected a list of answers",
        "closing: needs an answer",
    ]


def test_a_list_answer_must_hold_text() -> None:
    with pytest.raises(BuildError, match="rules: every answer must be text"):
        render_prompt(TEMPLATE, LABEL, {**ANSWERS, "rules": [1]})  # type: ignore[list-item]


def test_a_closed_choice_refuses_other_answers() -> None:
    from validia.prompts.building import Question, _items

    problems: list[str] = []
    closed = Question("c", "?", "choice", options=(Option("a"),))
    assert _items(closed, "b", problems) == ["b"]
    assert problems == ["c: 'b' is not one of the options"]


def test_answers_are_reviewed_one_at_a_time_or_all_at_once() -> None:
    assert [h.id for h in review_answer(TEMPLATE, "rules", "NEVER guess")] == [
        "caps",
        "reason",
        "forbid-only",
    ]
    assert review_answer(TEMPLATE, "rules", "") == ()
    found = review_answers(TEMPLATE, {**ANSWERS, "task": "be helpful", "rules": ["x", "y"]})
    assert {key: [h.id for h in hints] for key, hints in found.items()} == {
        "task": ["vague"],
        "rules": ["reason", "reason"],
    }
