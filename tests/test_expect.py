"""Grading one reply: what each answer type forgives, and what it does not."""

import pytest

from validia.suites.expect import NO_TOOL, Fields, Label, Reply, TextChecks, ToolCall, ToolUse
from validia.suites.suite import Case


def text(reply: str) -> Reply:
    return Reply(text=reply)


# -------------------------------------------------------------------- label


@pytest.mark.parametrize(
    "reply", ["urgent", "Urgent", "  URGENT \n", "urgent.", "**urgent**", '"urgent"']
)
def test_a_label_forgives_case_space_and_wrapping(reply: str) -> None:
    assert Label("urgent").check(text(reply)).passed


@pytest.mark.parametrize("reply", ["normal", "urgent, because the site is down", "not urgent"])
def test_a_label_must_be_the_label_alone(reply: str) -> None:
    verdict = Label("urgent").check(text(reply))
    assert not verdict.passed
    assert verdict.reason.startswith("expected 'urgent', got ")


def test_a_long_reply_is_clipped_in_the_reason() -> None:
    verdict = Label("urgent").check(text("word " * 40))
    assert verdict.reason.endswith("...'")
    assert len(verdict.reason) < 100


# --------------------------------------------------------------------- json


def test_json_fields_match() -> None:
    expected = Fields({"category": "bug", "priority": "high"}, required=("product",))
    reply = text('{"category": "bug", "priority": "high", "product": "web", "extra": 1}')
    assert expected.check(reply).passed


def test_a_json_fence_is_allowed() -> None:
    assert Fields({"a": 1}).check(text('```json\n{"a": 1}\n```')).passed
    assert Fields({"a": 1}).check(text('```\n{"a": 1}```')).passed


def test_every_wrong_or_missing_field_is_named() -> None:
    expected = Fields({"category": "bug", "priority": "high"}, required=("product",))
    verdict = expected.check(text('{"category": "billing"}'))
    assert not verdict.passed
    assert verdict.reason == (
        "product: missing; category: expected 'bug', got 'billing'; priority: missing"
    )


def test_a_required_key_is_not_reported_twice() -> None:
    verdict = Fields({"a": 1}, required=("a",)).check(text("{}"))
    assert verdict.reason == "a: missing"


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("not json", "not JSON: Expecting value at line 1"),
        ("[1, 2]", "expected a JSON object, got an array"),
        ('"bug"', "expected a JSON object, got a string"),
        ("3", "expected a JSON object, got a number"),
        ("null", "expected a JSON object, got a literal"),
    ],
)
def test_a_reply_that_is_not_an_object_fails(reply: str, reason: str) -> None:
    assert Fields({"a": 1}).check(text(reply)).reason == reason


def test_true_is_not_one() -> None:
    assert not Fields({"flag": 1}).check(text('{"flag": true}')).passed
    assert not Fields({"flag": True}).check(text('{"flag": 1}')).passed
    assert Fields({"flag": True}).check(text('{"flag": true}')).passed
    assert Fields({"n": 1}).check(text('{"n": 1.0}')).passed


# --------------------------------------------------------------------- text


def test_text_checks_ignore_case_and_spacing() -> None:
    checks = TextChecks(contains=("version history",), not_contains=("I don't know",))
    assert checks.check(text("Pro adds unlimited notebooks and Version\n  History.")).passed


def test_every_failed_text_check_is_named() -> None:
    checks = TextChecks(
        equals="Yes.", contains=("ZIP", "Export"), not_contains=("import",), matches=r"\d"
    )
    verdict = checks.check(text("Use Import to bring notes in"))
    assert not verdict.passed
    assert verdict.reason == (
        "expected 'Yes.', got 'Use Import to bring notes in'; missing 'ZIP'; "
        "missing 'Export'; says 'import'; does not match '\\\\d'"
    )


def test_equals_compares_the_whole_reply() -> None:
    assert TextChecks(equals="Yes, it does.").check(text("  yes,   it does. ")).passed


def test_matches_is_a_regex_as_written() -> None:
    assert not TextChecks(matches="yes").check(text("YES")).passed
    assert TextChecks(matches="(?i)yes").check(text("YES")).passed


# --------------------------------------------------------------------- tool


def calls(*made: tuple[str, dict[str, object]]) -> Reply:
    return Reply(tool_calls=tuple(ToolCall(name, dict(args)) for name, args in made))


def test_the_right_call_passes() -> None:
    expected = ToolUse("lookup_order", {"order_id": "48213"})
    assert expected.check(calls(("lookup_order", {"order_id": "48213", "verbose": True}))).passed


def test_unlisted_arguments_may_hold_anything() -> None:
    assert ToolUse("search_help").check(calls(("search_help", {"query": "billing email"}))).passed


def test_one_matching_call_among_several_is_enough() -> None:
    expected = ToolUse("lookup_order", {"order_id": "2"})
    reply = calls(("lookup_order", {"order_id": "1"}), ("lookup_order", {"order_id": "2"}))
    assert expected.check(reply).passed


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (
            calls(("search_help", {"query": "x"})),
            "expected a call to lookup_order, got search_help",
        ),
        (
            Reply(text="Let me check."),
            "expected a call to lookup_order, got no tool, said 'Let me check.'",
        ),
        (
            calls(("lookup_order", {"order_id": "9"})),
            "lookup_order: order_id: expected '48213', got '9'",
        ),
        (calls(("lookup_order", {})), "lookup_order: order_id: missing"),
    ],
)
def test_a_wrong_call_says_what_was_called(reply: Reply, reason: str) -> None:
    verdict = ToolUse("lookup_order", {"order_id": "48213"}).check(reply)
    assert not verdict.passed
    assert verdict.reason == reason


def test_no_tool_means_no_call() -> None:
    assert ToolUse(NO_TOOL).check(Reply(text="You're welcome!")).passed
    verdict = ToolUse(NO_TOOL).check(calls(("search_help", {}), ("lookup_order", {})))
    assert verdict.reason == "called search_help, lookup_order; expected no tool"


# ------------------------------------------------------------- empty replies


@pytest.mark.parametrize(
    "expected",
    [Label("urgent"), Fields({"a": 1}), TextChecks(not_contains=("x",)), ToolUse(NO_TOOL)],
)
def test_an_empty_reply_always_fails(expected: Label | Fields | TextChecks | ToolUse) -> None:
    case = Case(id="c", input="hi", expected=expected)
    verdict = case.check(Reply(text="  \n"))
    assert verdict == verdict.__class__(passed=False, reason="empty reply")


@pytest.mark.parametrize(
    ("expected", "described"),
    [
        (Label("urgent"), "is 'urgent'"),
        (Fields({"category": "bug"}, required=("id",)), "has category='bug'"),
        (Fields(required=("id", "name")), "is a JSON object with id, name"),
        (TextChecks(equals="Yes."), "is 'Yes.'"),
        (
            TextChecks(contains=("a", "b"), not_contains=("c",), matches=r"\d+"),
            r"contains 'a', 'b'; does not contain 'c'; matches /\d+/",
        ),
        (ToolUse("lookup", {"id": 7}), "calls lookup(id=7)"),
        (ToolUse("search"), "calls search, with any arguments"),
        (ToolUse(NO_TOOL), "calls no tool"),
    ],
)
def test_each_check_says_what_a_right_reply_is(
    expected: Label | Fields | TextChecks | ToolUse, described: str
) -> None:
    assert expected.describe() == described
