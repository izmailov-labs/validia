"""The run command's lines: plain without rich, coloured with it, the same text either way."""

import io
import sys

import pytest
from rich.console import Console

from validia.cli.output import Output


def terminal() -> tuple[Output, io.StringIO]:
    """An output on a fake colour terminal, and what it wrote."""
    written = io.StringIO()
    console = Console(
        file=written, force_terminal=True, color_system="standard", soft_wrap=True, highlight=False
    )
    return Output(console), written


def test_without_rich_the_lines_are_plain_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "rich.console", None)
    out = Output()
    out.row("FAIL", "case-1", 7, "missing 'ZIP'")
    out.row("PASS", "case-22", 7)
    out.line(("6 cases ready", "bold"), "; nothing sent.")
    with out.progress(2, "suite on model") as advance:
        advance()
    assert capsys.readouterr().out == (
        "  FAIL   case-1   missing 'ZIP'\n  PASS   case-22\n6 cases ready; nothing sent.\n"
    )


def test_off_a_terminal_rich_writes_the_same_plain_text(capsys: pytest.CaptureFixture[str]) -> None:
    out = Output()
    out.row("FAIL", "case-1", 7, "missing 'ZIP'")
    out.row("PASS", "case-22", 7)
    assert capsys.readouterr().out == "  FAIL   case-1   missing 'ZIP'\n  PASS   case-22\n"


def test_a_terminal_shows_each_status_in_its_colour() -> None:
    out, written = terminal()
    out.row("PASS", "a", 1)
    out.row("FAIL", "b", 1, "why")
    out.row("ERROR", "c", 1, "why")
    out.row("ready", "d", 1, "is 'urgent'")
    text = written.getvalue()
    assert "\x1b[1;32mPASS" in text  # bold green
    assert "\x1b[1;31mFAIL" in text  # bold red
    assert "\x1b[1;33mERROR" in text  # bold yellow
    assert "\x1b[36mready" in text  # cyan
    assert "\x1b[2mis 'urgent'" in text  # what a ready case expects, dimmed


def test_brackets_in_a_reason_are_text_not_markup() -> None:
    out, written = terminal()
    out.row("ERROR", "cases[6]", 8, "expected: 'low' is not one of the labels ['urgent', 'normal']")
    text = written.getvalue()
    assert "cases[6]" in text
    assert "['urgent', 'normal']" in text


def test_a_terminal_gets_a_progress_bar_that_clears_itself() -> None:
    out, written = terminal()
    with out.progress(3, "triage on anthropic:claude-x") as advance:
        for _ in range(3):
            advance()
    assert "triage on anthropic:claude-x" in written.getvalue()


def test_no_progress_bar_off_a_terminal() -> None:
    written = io.StringIO()
    out = Output(Console(file=written))
    with out.progress(3, "triage") as advance:
        advance()
    assert written.getvalue() == ""
