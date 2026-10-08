"""Prompt templates: the default, its core checks, rendering, and a broken template."""

from pathlib import Path

import pytest

from validia.prompts.template import (
    Hint,
    Section,
    TemplateError,
    default_template,
    default_text,
    fill_output,
    load_template,
)
from validia.suites.suite import Tool

TEMPLATE = default_template()


def test_the_default_template_loads_and_is_commented() -> None:
    assert [s.id for s in TEMPLATE.sections] == ["role", "task", "context", "rules", "tone"]
    assert [h.id for h in TEMPLATE.hints] == ["caps", "reason", "forbid-only", "vague"]
    assert TEMPLATE.source == "the built-in prompt template"
    assert "validia template > prompt.toml" in default_text()


def test_tone_is_asked_for_free_text_only() -> None:
    assert [s.id for s in TEMPLATE.sections_for("label")] == ["role", "task", "context", "rules"]
    assert TEMPLATE.sections_for("text")[-1].id == "tone"


@pytest.mark.parametrize(
    ("section", "answer", "fired"),
    [
        ("rules", "NEVER guess", ["caps", "reason", "forbid-only"]),
        ("rules", "judge by facts", ["reason"]),
        ("rules", "don't guess, because a wrong answer costs more", ["forbid-only"]),
        ("rules", "check the order first, so that the answer is current", []),
        ("task", "be helpful to customers", ["vague"]),
        ("role", "an ALWAYS-on assistant", ["caps"]),
        ("role", "the support assistant", []),
    ],
)
def test_the_core_checks(section: str, answer: str, fired: list[str]) -> None:
    assert [hint.id for hint in TEMPLATE.hints_for(section, answer)] == fired


def test_every_output_option_fills_in() -> None:
    tools = [Tool("lookup_order", {}, "Look up one order"), Tool("ping", {})]
    filled = {
        answer: [fill_output(o, labels=["a", "b", "c"], keys=["k"], tools=tools) for o in options]
        for answer, options in TEMPLATE.output.items()
    }
    assert filled["label"] == ["Reply with one word, a, b or c, and nothing else."]
    assert "exactly these keys: k." in filled["json"][0]
    assert "- lookup_order: Look up one order\n- ping\n" in filled["tool"][0]
    assert not any("{" in option for options in filled.values() for option in options)
    assert fill_output("{keys}", labels=[], keys=[], tools=[]) == "the fields the task calls for"
    assert fill_output("{labels}", labels=["only"], keys=[], tools=[]) == "only"


def test_sections_render_one_answer_or_many() -> None:
    assert Section("r", "?", template="You are {answer}.").render(["kind"]) == "You are kind."
    assert Section("r", "?").render([]) == ""
    many = Section("r", "?", many=True, heading="Rules:")
    assert many.render(["a", "b"]) == "Rules:\n- a\n- b"
    assert Section("r", "?", many=True, item="* {answer}").render(["a"]) == "* a"


def test_a_hint_reads_only_its_sections() -> None:
    hint = Hint("h", "m", pattern="x", sections=("rules",))
    assert hint.fires("rules", "x")
    assert not hint.fires("role", "x")


GOOD_OUTPUT = '[output]\nlabel = ["l"]\njson = ["j"]\ntext = ["t"]\ntool = ["o"]\n'


def broken(tmp_path: Path, text: str) -> str:
    path = tmp_path / "prompt.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TemplateError) as caught:
        load_template(path)
    return str(caught.value)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("sections = 1\n" + GOOD_OUTPUT, "sections: expected [[sections]] tables"),
        (GOOD_OUTPUT, "sections: give at least one [[sections]] table"),
        ('[[sections]]\nid = "a"\nask = "?"\ntemplte = "x"\n' + GOOD_OUTPUT, "did you mean"),
        ('[[sections]]\nid = "a"\n' + GOOD_OUTPUT, "sections[0].ask: missing"),
        (
            '[[sections]]\nid = "a"\nask = ""\n' + GOOD_OUTPUT,
            "sections[0].ask: expected a non-empty string",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\nrequired = "no"\n' + GOOD_OUTPUT,
            "required: expected true or false",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\nanswers = ["yaml"]\n' + GOOD_OUTPUT,
            "'yaml' is not an answer type",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\nmany = true\nchoices = ["x"]\n' + GOOD_OUTPUT,
            "drop one of them",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\n[[sections]]\nid = "a"\nask = "?"\n' + GOOD_OUTPUT,
            "'a' is used twice",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\n[[hints]]\nid = "h"\nmessage = "m"\n' + GOOD_OUTPUT,
            "give exactly one of `pattern` and `missing`",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\n[[hints]]\nid = "h"\nmessage = "m"\npattern = "("\n'
            + GOOD_OUTPUT,
            "hints[0].pattern: not a valid regular expression",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\n[[hints]]\nid = "h"\nmessage = "m"\npattern = "x"\nsections = ["b"]\n'
            + GOOD_OUTPUT,
            "no section 'b'",
        ),
        ('[[sections]]\nid = "a"\nask = "?"\n', "output: missing"),
        (
            '[[sections]]\nid = "a"\nask = "?"\n[output]\nlabel = ["l"]\n',
            "output.json: give at least one instruction",
        ),
        (
            '[[sections]]\nid = "a"\nask = "?"\n' + GOOD_OUTPUT + 'judge = ["x"]\n',
            "output.judge: no such field",
        ),
        ("[[sections]\n", "prompt.toml is not valid TOML"),
    ],
)
def test_a_broken_template_names_every_problem(tmp_path: Path, text: str, problem: str) -> None:
    assert problem in broken(tmp_path, text)


def test_a_project_template_loads(tmp_path: Path) -> None:
    path = tmp_path / "prompt.toml"
    path.write_text('[[sections]]\nid = "a"\nask = "?"\n' + GOOD_OUTPUT, encoding="utf-8")
    template = load_template(path)
    assert template.source == "prompt.toml"
    assert template.output["tool"] == ("o",)
