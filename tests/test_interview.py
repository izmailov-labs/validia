"""Terminal questions: menus, yes/no, multi-line text, and a suite's setup."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from validia.cli.interview import ANSWER_OPTIONS, CancelledByUserError, Interview
from validia.prompts.template import PromptTemplate, Section, default_template
from validia.suites.api import CaseSpec
from validia.suites.suite import ANSWER_TYPES, Grade, Tool

Feed = Callable[..., list[str]]


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    """Script ``input()``; the returned list records which replies were consumed."""

    def script(*replies: str) -> list[str]:
        pending = iter(replies)
        used: list[str] = []

        def fake_input() -> str:
            try:
                reply = next(pending)
            except StopIteration:
                raise EOFError from None  # the script ran out: what Ctrl-D does
            used.append(reply)
            return reply

        monkeypatch.setattr("builtins.input", fake_input)
        return used

    return script


ask = Interview()


def test_the_answer_menu_lists_every_type_in_order() -> None:
    assert tuple(answer for answer, _ in ANSWER_OPTIONS) == ANSWER_TYPES


@pytest.mark.parametrize(("reply", "chosen"), [("2", "json"), ("tool", "tool"), ("", "label")])
def test_choose_takes_a_number_a_name_or_the_default(
    feed: Feed, capsys: pytest.CaptureFixture[str], reply: str, chosen: str
) -> None:
    feed(reply)
    assert ask.answer_type() == chosen
    err = capsys.readouterr().err
    assert "  2  json   a JSON object\n" in err
    assert "Choose 1-4 [1]: " in err


def test_choose_asks_again_until_the_answer_is_an_option(
    feed: Feed, capsys: pytest.CaptureFixture[str]
) -> None:
    used = feed("0", "5", "yaml", "3")
    assert ask.choose("Pick", [("a", ""), ("b", ""), ("c", "")]) == "c"
    assert used == ["0", "5", "yaml", "3"]
    assert capsys.readouterr().err.count("choose 1-3, or a name: a, b, c") == 3


def test_choose_without_a_default_needs_an_answer(feed: Feed) -> None:
    used = feed("", "b")
    assert ask.choose("Pick", [("a", ""), ("b", "")]) == "b"
    assert used == ["", "b"]


def test_a_finish_lets_an_empty_answer_stop(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("")
    assert ask.choose("Pick", [("a", "")], finish="empty to stop") == ""
    assert "Choose 1-1 (empty to stop): " in capsys.readouterr().err


@pytest.mark.parametrize(
    ("replies", "default", "answer"),
    [
        (("",), True, True),
        (("",), False, False),
        (("Y",), False, True),
        (("maybe", "no"), True, False),
    ],
)
def test_confirm(feed: Feed, replies: tuple[str, ...], default: bool, answer: bool) -> None:
    feed(*replies)
    assert ask.confirm("Go on?", default=default) is answer


def test_a_block_keeps_blank_lines_until_a_dot(feed: Feed) -> None:
    feed("", "First paragraph.", "", "Second paragraph.  ", "", ".")
    assert ask.block("Write it") == "First paragraph.\n\nSecond paragraph.\n"


def test_a_block_cut_short_is_cancelled(feed: Feed) -> None:
    feed("only one line")
    with pytest.raises(CancelledByUserError, match="nothing was written"):
        ask.block("Write it")


def test_labels_need_two_distinct_ones(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("yes", "yes, yes", "yes, no, yes")
    assert ask.labels() == ("yes", "no")
    assert capsys.readouterr().err.count("list at least two") == 2


def test_required_keys_may_be_none(feed: Feed) -> None:
    feed("")
    assert ask.required() == ()


def test_tools_are_described_one_at_a_time(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed(
        "",
        "none",
        "bad name",
        "lookup_order",
        "Look up an order",
        "order_id, verbose",
        "lookup_order",
        "search_help",
        "Search help",
        "",
        "",
    )
    tools = ask.tools()
    err = capsys.readouterr().err
    assert "a tool suite needs at least one tool" in err
    assert "'none' means no call at all" in err
    assert "use letters, digits" in err
    assert "'lookup_order' is already defined" in err
    assert [tool.name for tool in tools] == ["lookup_order", "search_help"]
    assert tools[0].parameters == {
        "type": "object",
        "properties": {"order_id": {"type": "string"}, "verbose": {"type": "string"}},
        "required": ["order_id", "verbose"],
    }
    assert tools[1].parameters["properties"] == {}


def test_a_tools_file_is_checked_as_it_is_named(
    feed: Feed, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bad.json").write_text("[{}]", encoding="utf-8")
    good = [{"name": "a", "parameters": {"type": "object"}}]
    (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
    feed("missing.json", "bad.json", "good.json")
    assert ask.tools_file(tmp_path) == tmp_path / "good.json"
    err = capsys.readouterr().err
    assert "no such file 'missing.json'" in err
    assert "bad.json[0].name: missing" in err


def test_a_case_offers_existing_groups(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("", "Hello", "greeting, small", "1")
    grade = Grade("label", labels=("greeting", "question"))
    case = ask.case(grade, (), {"case-1"}, ["greeting", "question"])
    assert case == CaseSpec("case-2", "Hello", "greeting", ("greeting", "small"))
    assert "Tags, comma-separated; the first groups results (greeting, question): " in (
        capsys.readouterr().err
    )


def test_a_reply_for_a_tool_suite(feed: Feed) -> None:
    tools = (Tool("lookup_order", {"type": "object"}, "Look up an order"),)
    grade = Grade("tool")
    feed("1", '{"order_id": "5"}')
    reply = ask.reply(grade, tools, finish="empty to stop")
    assert reply is not None
    assert reply.tool_calls[0].args == {"order_id": "5"}
    feed("none")
    assert ask.reply(grade, tools, finish="empty to stop") is not None
    feed("")
    assert ask.reply(grade, tools, finish="empty to stop") is None


# ---------------------------------------------------------- building prompts

BUILT_IN = default_template()


def test_a_hint_can_be_kept(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("judge by facts", "y")
    assert ask.checked(BUILT_IN, "rules", "Rule", required=True) == "judge by facts"
    assert "hint reason: give the reason" in capsys.readouterr().err


def test_a_text_prompt_offers_tones_and_closings(
    feed: Feed, capsys: pytest.CaptureFixture[str]
) -> None:
    feed(
        "the help desk for Acme Notes",
        "answer questions from the help notes",
        "The notes cover export, offline use and plans.",
        "",
        "2",  # warm and friendly
        "2",  # one short paragraph
        "",
    )
    text = ask.build_prompt(BUILT_IN, Grade("text"), ())
    assert text.endswith("Write in a warm and friendly way.\n\nAnswer in one short paragraph.\n")
    assert "The notes cover export, offline use and plans.\n\n" in text
    err = capsys.readouterr().err
    assert "  e.g. the support assistant for Acme Shop" in err
    assert "  | Answer in one short paragraph." in err


def test_tone_can_be_written_or_skipped(feed: Feed) -> None:
    feed("a tutor", "explain fractions", "", "", "own", "patient", "", "")
    assert "Write in a patient way." in ask.build_prompt(BUILT_IN, Grade("text"), ())
    feed("a tutor", "explain fractions", "", "", "skip", "", "")
    assert "Write in" not in ask.build_prompt(BUILT_IN, Grade("text"), ())


def test_a_closing_of_ones_own_and_starting_over(feed: Feed) -> None:
    tools = (Tool("lookup_order", {"type": "object"}, "Look up one order"),)
    feed(
        "the shop assistant",
        "route questions to tools",
        "",
        "",
        "",
        "n",  # first closing, then reject the prompt: start over
        "the shop assistant",
        "route questions to tools",
        "",
        "",
        "2",
        "Call lookup_order for orders.",
        "",
    )
    text = ask.build_prompt(BUILT_IN, Grade("tool"), tools)
    assert text.endswith("Call lookup_order for orders.\n")


def test_a_required_many_section_needs_one_answer(
    feed: Feed, capsys: pytest.CaptureFixture[str]
) -> None:
    template = PromptTemplate(
        sections=(Section("steps", "A step", many=True, heading="Steps:"),),
        hints=(),
        output={"label": ("Say {labels}.",), "json": ("j",), "text": ("t",), "tool": ("o",)},
        source="test",
    )
    feed("", "look", "act", "", "", "")
    text = ask.build_prompt(template, Grade("label", labels=("yes", "no")), ())
    assert text == "Steps:\n- look\n- act\n\nSay yes or no.\n"
    assert "this part of the prompt needs an answer" in capsys.readouterr().err


def test_a_menu_takes_numbers_only(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("first", "9", "2")
    assert ask.menu("Pick", ["one\ncontinued", "two"]) == 2
    err = capsys.readouterr().err
    assert "  1  one\n     continued\n" in err
    assert err.count("choose a number from 1 to 2") == 2
