"""Suite files: the shipped examples, and every way a hand-written one can be wrong."""

import json
from collections import Counter
from pathlib import Path, PurePosixPath

import pytest

from validia.suites import scaffold
from validia.suites.expect import NO_TOOL, Fields, Label, Reply, TextChecks, ToolCall, ToolUse
from validia.suites.suite import (
    ANSWER_TYPES,
    AnswerType,
    Case,
    Grade,
    Suite,
    SuiteError,
    load_suite,
)

VALID = """\
prompt = "prompt.md"

[grade]
type = "label"
labels = ["yes", "no"]

[[cases]]
id = "one"
input = "Is water wet?"
expected = "yes"
"""

TOOLS = [
    {
        "name": "lookup_order",
        "description": "Look up an order.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    }
]


def suite_file(tmp_path: Path, text: str, *, prompt: bool = True) -> Path:
    if prompt:
        (tmp_path / "prompt.md").write_text("Answer yes or no.\n", encoding="utf-8")
    path = tmp_path / "suite.toml"
    path.write_text(text, encoding="utf-8")
    return path


def tools_file(tmp_path: Path, tools: object) -> None:
    text = tools if isinstance(tools, str) else json.dumps(tools)
    (tmp_path / "tools.json").write_text(text, encoding="utf-8")


def tool_suite(tmp_path: Path, expected: str, *, tools: object = TOOLS) -> Path:
    tools_file(tmp_path, tools)
    text = f"""\
prompt = "prompt.md"
tools = "tools.json"

[grade]
type = "tool"

[[cases]]
id = "one"
input = "Where is order 1?"
expected = {expected}
"""
    return suite_file(tmp_path, text)


def problems(path: Path) -> str:
    with pytest.raises(SuiteError) as caught:
        load_suite(path)
    return str(caught.value)


# ------------------------------------------------------------ the examples


def example(tmp_path: Path, answer: AnswerType) -> Suite:
    for name in scaffold.suite_files(answer):
        rendered = scaffold.render_suite(answer, name, PurePosixPath("suite.toml"))
        (tmp_path / name).write_text(rendered, encoding="utf-8")
    return load_suite(tmp_path / "suite.toml")


def right_answer(case: Case) -> Reply:
    """The reply a perfect model would give, for the answer types that have one."""
    expected = case.expected
    if isinstance(expected, Label):
        return Reply(text=expected.value)
    if isinstance(expected, Fields):
        return Reply(text=json.dumps(expected.values))
    if isinstance(expected, ToolUse) and expected.tool != NO_TOOL:
        return Reply(tool_calls=(ToolCall(expected.tool, expected.args),))
    return Reply(text="Hello! How can I help?")


@pytest.mark.parametrize("answer", ANSWER_TYPES)
def test_every_example_suite_loads(tmp_path: Path, answer: AnswerType) -> None:
    suite = example(tmp_path, answer)
    assert suite.grade.type == answer
    assert suite.prompt == tmp_path / "prompt.md"
    assert len(suite.cases) >= 6
    assert suite.description
    assert "validia run suite.toml --model MODEL --dry-run" in suite.path.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("answer", ["label", "json", "tool"])
def test_right_answers_pass_every_example(tmp_path: Path, answer: AnswerType) -> None:
    """The oracle check: if the reference answers fail, the grader is broken."""
    suite = example(tmp_path, answer)
    failed = {case.id: case.check(right_answer(case)).reason for case in suite.cases}
    assert {key: reason for key, reason in failed.items() if reason} == {}


@pytest.mark.parametrize("answer", ANSWER_TYPES)
def test_an_empty_reply_fails_every_example(tmp_path: Path, answer: AnswerType) -> None:
    """The null check: saying nothing must score zero, whatever the answer type."""
    suite = example(tmp_path, answer)
    assert not any(case.check(Reply()).passed for case in suite.cases)


def test_the_label_example_is_balanced(tmp_path: Path) -> None:
    suite = example(tmp_path, "label")
    labels = [case.expected.value for case in suite.cases if isinstance(case.expected, Label)]
    assert Counter(labels) == {"urgent": 4, "normal": 4}
    always_urgent = sum(case.check(Reply(text="urgent")).passed for case in suite.cases)
    assert always_urgent == len(suite.cases) / 2


def test_the_label_example_tests_both_sides_of_its_hardest_mistake(tmp_path: Path) -> None:
    suite = example(tmp_path, "label")
    misleading = {
        case.expected.value
        for case in suite.cases
        if "misleading-tone" in case.tags and isinstance(case.expected, Label)
    }
    assert misleading == {"urgent", "normal"}


def test_the_json_example_covers_every_category_and_an_unknown(tmp_path: Path) -> None:
    suite = example(tmp_path, "json")
    assert suite.grade.required == ("category", "priority", "product")
    fields = [case.expected.values for case in suite.cases if isinstance(case.expected, Fields)]
    assert {f["category"] for f in fields} == {"bug", "billing", "account", "how-to"}
    assert {f["priority"] for f in fields} == {"high", "low"}
    assert "unknown" in {f["product"] for f in fields}


def test_the_text_example_is_half_uncovered(tmp_path: Path) -> None:
    suite = example(tmp_path, "text")
    assert suite.groups() == {"covered": 3, "not-covered": 3}
    assert all(isinstance(case.expected, TextChecks) for case in suite.cases)


def test_the_tool_example_needs_every_tool_and_sometimes_none(tmp_path: Path) -> None:
    suite = example(tmp_path, "tool")
    offered = {tool.name for tool in suite.tools}
    assert offered == {"lookup_order", "search_help", "create_ticket"}
    expected = Counter(
        case.expected.tool for case in suite.cases if isinstance(case.expected, ToolUse)
    )
    assert set(expected) == offered | {NO_TOOL}
    assert expected[NO_TOOL] == len(suite.cases) / 3
    assert suite.summary() == "tool  (lookup_order, search_help, create_ticket)"


# ------------------------------------------------------------ a valid file


def test_a_minimal_suite(tmp_path: Path) -> None:
    suite = load_suite(suite_file(tmp_path, VALID))
    assert suite.cases == (Case(id="one", input="Is water wet?", expected=Label("yes")),)
    assert suite.description == ""
    assert suite.groups() == {"untagged": 1}
    assert suite.summary() == "label  (yes, no)"


def test_a_json_suite_with_only_required_keys(tmp_path: Path) -> None:
    text = VALID.replace(
        'type = "label"\nlabels = ["yes", "no"]', 'type = "json"\nrequired = ["answer"]'
    )
    suite = load_suite(suite_file(tmp_path, text.replace('expected = "yes"', "expected = {}")))
    assert suite.cases[0].expected == Fields({}, required=("answer",))
    assert suite.summary() == "json  (required: answer)"


def test_a_text_suite(tmp_path: Path) -> None:
    text = VALID.replace('type = "label"\nlabels = ["yes", "no"]', 'type = "text"')
    text = text.replace(
        'expected = "yes"', "expected = { contains = [\"wet\"], matches = '(?i)yes' }"
    )
    suite = load_suite(suite_file(tmp_path, text))
    assert suite.cases[0].expected == TextChecks(contains=("wet",), matches="(?i)yes")
    assert suite.summary() == "text"
    assert Grade("json").type == "json"


# --------------------------------------------------------------- problems


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    text = """\
promt = "prompt.md"

[grade]
type = "label"
labels = ["yes", "no"]

[[cases]]
id = "one"
input = "Is water wet?"
expected = "maybe"

[[cases]]
id = "one"
tags = "wet"
expected = "no"
"""
    message = problems(suite_file(tmp_path, text))
    assert "has 6 problems" in message
    assert "promt: no such field - did you mean 'prompt'?" in message
    assert "prompt: missing" in message
    assert "cases[0].expected: 'maybe' is not one of the labels" in message
    assert "cases[1].input: missing" in message
    assert "cases[1].id: 'one' is already used by cases[0]" in message
    assert "cases[1].tags: expected a list of non-empty strings" in message


def test_an_unknown_answer_type_is_named(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, VALID.replace('type = "label"', 'type = "judge"')))
    assert "grade.type: 'judge' is not an answer type" in message
    assert "'label', 'json', 'text', 'tool'" in message


def test_a_missing_answer_type_is_named(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, VALID.replace('type = "label"\n', "")))
    assert "grade.type: missing" in message


def test_a_wrong_field_type_is_named(tmp_path: Path) -> None:
    message = problems(
        suite_file(tmp_path, VALID.replace('id = "one"', 'id = "one"\ntags = "wet"'))
    )
    assert "has 1 problem:" in message
    assert "cases[0].tags: expected a list of non-empty strings" in message


def test_an_empty_string_is_not_a_value(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, VALID.replace('"Is water wet?"', '"  "')))
    assert "cases[0].input: expected a non-empty string" in message


def test_the_prompt_file_must_exist(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, VALID, prompt=False))
    assert "prompt: no such file 'prompt.md' next to the suite" in message


def test_grade_and_cases_are_required(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, 'prompt = "prompt.md"\n'))
    assert "grade: missing" in message
    assert "cases: expected at least one [[cases]] table" in message


@pytest.mark.parametrize(
    ("labels", "problem"),
    [("labels = []", "needs at least one label"), ("", "grade.labels: missing")],
)
def test_a_label_grader_needs_labels(tmp_path: Path, labels: str, problem: str) -> None:
    message = problems(suite_file(tmp_path, VALID.replace('labels = ["yes", "no"]', labels)))
    assert problem in message


def test_grade_fields_belong_to_their_answer_type(tmp_path: Path) -> None:
    text = VALID.replace('labels = ["yes", "no"]', 'labels = ["yes", "no"]\nrequired = ["x"]')
    assert "grade.required: no such field" in problems(suite_file(tmp_path, text))


def test_a_case_must_be_a_table(tmp_path: Path) -> None:
    text = 'prompt = "prompt.md"\ncases = ["one"]\n[grade]\ntype = "label"\nlabels = ["yes"]\n'
    assert "cases[0]: expected a table" in problems(suite_file(tmp_path, text))


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    message = problems(suite_file(tmp_path, "prompt = \n"))
    assert "suite.toml is not valid TOML" in message


# ----------------------------------------------------------- json problems


def json_suite(tmp_path: Path, expected: str, required: str = "") -> Path:
    grade = f'type = "json"\n{required}'.rstrip()
    text = VALID.replace('type = "label"\nlabels = ["yes", "no"]', grade)
    return suite_file(tmp_path, text.replace('expected = "yes"', f"expected = {expected}"))


def test_json_expected_must_be_a_table(tmp_path: Path) -> None:
    message = problems(json_suite(tmp_path, '"yes"'))
    assert "cases[0].expected: expected a table, as in { category" in message


def test_json_expected_must_check_something(tmp_path: Path) -> None:
    message = problems(json_suite(tmp_path, "{}"))
    assert "cases[0].expected: checks nothing; list fields, or set grade.required" in message


def test_json_expected_is_required(tmp_path: Path) -> None:
    text = VALID.replace('type = "label"\nlabels = ["yes", "no"]', 'type = "json"')
    message = problems(suite_file(tmp_path, text.replace('expected = "yes"\n', "")))
    assert "cases[0].expected: missing" in message


# ----------------------------------------------------------- text problems


def text_suite(tmp_path: Path, expected: str) -> Path:
    text = VALID.replace('type = "label"\nlabels = ["yes", "no"]', 'type = "text"')
    return suite_file(tmp_path, text.replace('expected = "yes"', f"expected = {expected}"))


@pytest.mark.parametrize(
    ("expected", "problem"),
    [
        ('"yes"', 'expected a table, as in { contains = ["Export"] }'),
        ("{}", "expected: no checks; use equals, contains, not_contains, matches"),
        (
            '{ contain = ["yes"] }',
            "expected.contain: no such field - did you mean 'contains'?",
        ),
        ("{ matches = '(unclosed' }", "expected.matches: not a valid regular expression"),
        ("{ matches = 'yes|' }", "an empty reply matches it too, so it checks nothing"),
        ('{ contains = "yes" }', "expected.contains: expected a list of non-empty strings"),
    ],
)
def test_text_checks_that_cannot_work_are_refused(
    tmp_path: Path, expected: str, problem: str
) -> None:
    assert problem in problems(text_suite(tmp_path, expected))


# ----------------------------------------------------------- tool problems


def test_a_tool_suite_names_its_tools(tmp_path: Path) -> None:
    text = """\
prompt = "prompt.md"

[grade]
type = "tool"

[[cases]]
id = "one"
input = "Hi"
expected = { tool = "none" }
"""
    assert "tools: missing" in problems(suite_file(tmp_path, text))


def test_the_tools_file_must_exist(tmp_path: Path) -> None:
    path = tool_suite(tmp_path, '{ tool = "none" }')
    (tmp_path / "tools.json").unlink()
    assert "tools: no such file 'tools.json' next to the suite" in problems(path)


@pytest.mark.parametrize(
    ("tools", "problem"),
    [
        ("[", "tools: tools.json is not valid JSON"),
        ("{}", "tools: tools.json must be a JSON array of tool definitions"),
        ("[]", "tools: tools.json must be a JSON array of tool definitions"),
        ('["lookup_order"]', "tools.json[0]: expected an object"),
        ('[{"name": "none", "parameters": {}}]', "'none' is reserved"),
        (
            '[{"name": "a", "parameters": []}]',
            "tools.json[0].parameters: expected a JSON Schema object",
        ),
        ('[{"name": "a", "parameters": {}, "strict": "yes"}]', "strict: expected true or false"),
        ('[{"name": "a", "parameters": {}, "params": {}}]', "tools.json[0].params: no such field"),
        (
            '[{"name": "a", "parameters": {}}, {"name": "a", "parameters": {}}]',
            "tools.json[1].name: 'a' is defined twice",
        ),
    ],
)
def test_a_broken_tools_file_is_explained(tmp_path: Path, tools: str, problem: str) -> None:
    assert problem in problems(tool_suite(tmp_path, '{ tool = "none" }', tools=tools))


@pytest.mark.parametrize(
    ("expected", "problem"),
    [
        (
            '{ tool = "lookup_ordr" }',
            "'lookup_ordr' is not one of the tools - did you mean 'lookup_order'?",
        ),
        (
            '{ tool = "none", args = { order_id = "1" } }',
            "a case that expects no tool has none to check",
        ),
        (
            '{ tool = "lookup_order", args = { order = "1" } }',
            "args.order: no such field - did you mean 'order_id'?",
        ),
        ('{ tool = "lookup_order", args = "1" }', "expected.args: expected a table"),
        ('{ args = { order_id = "1" } }', "expected.tool: missing"),
        ('"lookup_order"', 'expected a table, as in { tool = "lookup_order" }'),
    ],
)
def test_a_tool_case_must_name_a_real_tool_and_arguments(
    tmp_path: Path, expected: str, problem: str
) -> None:
    assert problem in problems(tool_suite(tmp_path, expected))


def test_a_valid_tool_case(tmp_path: Path) -> None:
    suite = load_suite(tool_suite(tmp_path, '{ tool = "lookup_order", args = { order_id = "1" } }'))
    assert suite.cases[0].expected == ToolUse("lookup_order", {"order_id": "1"})
    assert suite.tools[0].name == "lookup_order"
    assert suite.tools[0].strict is None
