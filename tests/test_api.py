"""The front-end-agnostic API, used the way a REST handler would: JSON in, JSON out."""

import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

import validia
from validia.rules import CATEGORIES
from validia.suites.api import (
    CaseSpec,
    SpecError,
    SuiteSpec,
    add_case,
    check_reply,
    create_suite,
    describe_suite,
    find_case,
    locate_suite,
    parse_reply,
)
from validia.suites.expect import Reply

BODY = {
    "name": "triage",
    "answer": "label",
    "labels": ["urgent", "normal"],
    "prompt": "Reply with one word, urgent or normal.",
    "description": "Urgent vs normal",
    "cases": [
        {"id": "outage", "input": "Checkout is down", "expected": "urgent", "tags": ["urgent"]},
        {"id": "typo", "input": "Typo on pricing", "expected": "normal"},
    ],
}


def roundtrip(data: object) -> object:
    """What a JSON request body or response goes through."""
    return json.loads(json.dumps(data))


def test_the_api_is_public() -> None:
    for name in ("SuiteSpec", "create_suite", "add_case", "check_reply", "render_prompt"):
        assert name in validia.__all__


def test_create_a_suite_from_a_json_body(tmp_path: Path) -> None:
    spec = SuiteSpec.from_dict(roundtrip(BODY))  # type: ignore[arg-type]
    suite = create_suite(spec, tmp_path)
    assert suite.path == tmp_path / "evals/triage/suite.toml"
    described = describe_suite(suite, tmp_path)
    assert roundtrip(described) == described
    assert described["path"] == "evals/triage/suite.toml"
    assert described["prompt"] == "evals/triage/prompt.md"
    assert described["groups"] == {"urgent": 1, "untagged": 1}
    assert [case["id"] for case in described["cases"]] == ["outage", "typo"]
    assert described["rules"] == list(CATEGORIES)
    assert (tmp_path / "evals/triage/prompt.md").read_text(encoding="utf-8").endswith(".\n")


def test_a_malformed_body_lists_every_problem() -> None:
    body = {
        "nme": "x",
        "answer": "yaml",
        "cases": [{"id": 1, "input": "x"}, "not an object"],
        "labels": "a",
        "tools": {},
    }
    with pytest.raises(SpecError) as caught:
        SuiteSpec.from_dict(body)
    assert caught.value.problems == [
        "nme: no such field - did you mean 'name'?",
        "name: missing",
        "answer: 'yaml' is not one of ['label', 'json', 'text', 'tool']",
        "cases[0].expected: missing",
        "cases[0].id: expected text",
        "cases[1]: expected an object",
        "tools: expected a list",
        "labels: expected a list of text",
    ]


def test_a_spec_that_contradicts_itself_writes_nothing(tmp_path: Path) -> None:
    spec = SuiteSpec(
        name="bad name",
        answer="text",
        cases=(CaseSpec("a", "x", {"contains": ["y"]}), CaseSpec("a", "", {"contains": ["y"]})),
        folder="../out",
        labels=("one",),
        required=("k",),
        tools=(validia.Tool("none", {}),),
    )
    with pytest.raises(SpecError) as caught:
        create_suite(spec, tmp_path)
    assert caught.value.problems == [
        "name: use letters, digits, '.', '_' and '-', with no slashes",
        "folder: use a folder inside the project, as in evals",
        "labels: only a label suite has labels",
        "required: only a json suite has required keys",
        "tools: only a tool suite has tools",
        "prompt: give exactly one of prompt and prompt_file",
        "tools[0].name: 'none' means no call at all",
        "cases[1].id: 'a' is already a case",
        "cases[1].input: a case needs an input",
    ]
    assert list(tmp_path.iterdir()) == []


def test_answer_specific_needs_are_checked() -> None:
    label = SuiteSpec("a", "label", (CaseSpec("c", "x", "y"),), labels=("y",), prompt="p")
    assert "labels: a label suite needs at least two" in label.problems()
    tool = SuiteSpec("a", "tool", (CaseSpec("c", "x", {"tool": "none"}),), prompt="p")
    assert "tools: a tool suite needs exactly one of tools and tools_file" in tool.problems()
    tools = (validia.Tool("t", {}), validia.Tool("t", {}), validia.Tool("bad name", {}))
    twice = SuiteSpec("a", "tool", (CaseSpec("c", "x", {"tool": "t"}),), prompt="p", tools=tools)
    assert "tools[1].name: 't' is defined twice" in twice.problems()
    assert any(p.startswith("tools[2].name: use letters") for p in twice.problems())
    empty = SuiteSpec("a", "text", (), prompt="p")
    assert "cases: a suite needs at least one case" in empty.problems()


def test_a_case_whose_shape_does_not_fit_is_reported_from_the_loader(tmp_path: Path) -> None:
    body = {**BODY, "cases": [{"id": "x", "input": "y", "expected": "maybe"}]}
    with pytest.raises(SpecError) as caught:
        create_suite(SuiteSpec.from_dict(body), tmp_path)
    assert caught.value.problems == [
        "cases[0].expected: 'maybe' is not one of the labels ['urgent', 'normal']"
    ]
    assert not (tmp_path / "evals/triage").exists()


def test_a_value_toml_cannot_hold_is_reported(tmp_path: Path) -> None:
    body = {
        **BODY,
        "answer": "json",
        "labels": [],
        "cases": [{"id": "x", "input": "y", "expected": {"a": None}}],
    }
    with pytest.raises(SpecError) as caught:
        create_suite(SuiteSpec.from_dict(body), tmp_path)
    assert caught.value.problems[0].startswith("cases[0].expected: TOML cannot hold None")
    assert not (tmp_path / "evals").exists()


def test_a_suite_can_point_at_existing_prompt_and_tools(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/prompt.md").write_text("Route it.\n", encoding="utf-8")
    tools = [
        {
            "name": "lookup_order",
            "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    ]
    (tmp_path / "shared/tools.json").write_text(json.dumps(tools), encoding="utf-8")
    body = {
        "name": "router",
        "answer": "tool",
        "prompt_file": "shared/prompt.md",
        "tools_file": "shared/tools.json",
        "cases": [
            {
                "id": "a",
                "input": "Where is 1?",
                "expected": {"tool": "lookup_order", "args": {"id": "1"}},
            }
        ],
    }
    suite = create_suite(SuiteSpec.from_dict(body), tmp_path)
    text = suite.path.read_text(encoding="utf-8")
    assert 'prompt = "../../shared/prompt.md"' in text
    assert 'tools = "../../shared/tools.json"' in text
    assert sorted(path.name for path in suite.path.parent.iterdir()) == ["suite.toml"]


def test_missing_references_and_taken_names_are_refused(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/tools.json").write_text("[{}]", encoding="utf-8")
    body = {**BODY, "prompt": "", "prompt_file": "nope.md"}
    with pytest.raises(SpecError, match=re.escape("prompt_file: no such file 'nope.md'")):
        create_suite(SuiteSpec.from_dict(body), tmp_path)
    tool = {
        "name": "t",
        "answer": "tool",
        "prompt": "p",
        "tools_file": "shared/tools.json",
        "cases": [{"id": "a", "input": "x", "expected": {"tool": "none"}}],
    }
    with pytest.raises(SpecError, match=re.escape("tools_file: tools.json[0].name: missing")):
        create_suite(SuiteSpec.from_dict(tool), tmp_path)
    create_suite(SuiteSpec.from_dict(BODY), tmp_path)
    with pytest.raises(SpecError, match="name: evals/triage already exists"):
        create_suite(SuiteSpec.from_dict(BODY), tmp_path)


def test_tools_given_as_definitions_are_written(tmp_path: Path) -> None:
    body = {
        "name": "router",
        "answer": "tool",
        "prompt": "Route it.",
        "tools": [{"name": "ping", "description": "Check it is up"}, "not a tool"],
        "cases": [{"id": "a", "input": "Up?", "expected": {"tool": "ping"}}],
    }
    with pytest.raises(SpecError, match=re.escape("tools[1]: expected an object")):
        SuiteSpec.from_dict(body)
    body["tools"] = [{"name": "ping", "description": "Check it is up", "parameters": []}]
    with pytest.raises(SpecError, match="parameters: expected a JSON Schema object"):
        SuiteSpec.from_dict(body)
    body["tools"] = [{"name": "ping", "description": "Check it is up"}]
    suite = create_suite(SuiteSpec.from_dict(body), tmp_path)
    assert (
        json.loads((suite.path.parent / "tools.json").read_text(encoding="utf-8"))[0]["name"]
        == "ping"
    )


@pytest.fixture
def suite_path(tmp_path: Path) -> Path:
    return create_suite(SuiteSpec.from_dict(BODY), tmp_path).path


def test_add_a_case_from_a_json_body(suite_path: Path) -> None:
    case = CaseSpec.from_dict({"id": "refund", "input": "Refund please", "expected": "normal"})
    suite = add_case(suite_path, case)
    assert [c.id for c in suite.cases] == ["outage", "typo", "refund"]


def test_a_case_that_does_not_fit_is_refused(suite_path: Path) -> None:
    with pytest.raises(SpecError, match="'outage' is already a case"):
        add_case(suite_path, CaseSpec("outage", "x", "urgent"))
    before = suite_path.read_text(encoding="utf-8")
    with pytest.raises(SpecError, match="'maybe' is not one of the labels"):
        add_case(suite_path, CaseSpec("new", "x", "maybe"))
    assert suite_path.read_text(encoding="utf-8") == before
    with pytest.raises(SpecError, match="expected: missing"):
        CaseSpec.from_dict({"id": "x", "input": "y"})
    assert CaseSpec("x", "y", None).problems() == ["expected: missing"]


def test_check_a_reply_from_a_json_body(suite_path: Path) -> None:
    suite = validia.load_suite(suite_path)
    verdict = check_reply(suite, "outage", parse_reply({"text": "Urgent."}))
    assert asdict(verdict) == {"passed": True, "reason": ""}
    failed = check_reply(suite, "typo", parse_reply({"text": "urgent"}))
    assert roundtrip(asdict(failed)) == {
        "passed": False,
        "reason": "expected 'normal', got 'urgent'",
    }
    with pytest.raises(
        SpecError, match=re.escape("no case 'outge' in suite.toml; did you mean 'outage'")
    ):
        find_case(suite, "outge")


def test_a_reply_with_tool_calls_parses() -> None:
    reply = parse_reply({"tool_calls": [{"name": "lookup_order", "args": {"id": "1"}}]})
    assert reply == Reply(tool_calls=(validia.ToolCall("lookup_order", {"id": "1"}),))
    with pytest.raises(SpecError) as caught:
        parse_reply({"txt": "x", "tool_calls": [{"args": {}}, {"name": "a", "args": [], "x": 1}]})
    assert caught.value.problems == [
        "txt: no such field - did you mean 'text'?",
        "tool_calls[0].name: expected text",
        "tool_calls[1].x: no such field",
        "tool_calls[1].args: expected an object",
    ]
    with pytest.raises(SpecError, match="tool_calls: expected a list"):
        parse_reply({"tool_calls": {}})


# ------------------------------------------------- staying inside the project


def symlink(link: Path, target: Path) -> None:
    """Make a symlink, or skip where the platform will not let this user make one."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:  # probed, not assumed: Windows needs a privilege for this
        pytest.skip(f"cannot create symlinks here: {exc}")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_file", "../outside.md"),
        ("prompt_file", "/etc/passwd"),
        ("tools_file", "../tools.json"),
    ],
)
def test_referenced_files_must_be_inside_the_project(
    tmp_path: Path, field: str, value: str
) -> None:
    body = {**BODY, "prompt": ""} if field == "prompt_file" else {**BODY}
    body[field] = value
    with pytest.raises(SpecError) as caught:
        create_suite(SuiteSpec.from_dict(body), tmp_path)
    assert f"{field}: use a path inside the project, as in evals" in caught.value.problems
    assert list(tmp_path.iterdir()) == []


def test_a_symlink_out_of_the_project_is_refused(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "prompt.md").write_text("secret\n", encoding="utf-8")
    symlink(project / "shared", outside)
    body = {**BODY, "prompt": "", "prompt_file": "shared/prompt.md"}
    with pytest.raises(SpecError) as caught:
        create_suite(SuiteSpec.from_dict(body), project)
    assert caught.value.problems == ["prompt_file: 'shared/prompt.md' leads outside the project"]
    assert sorted(path.name for path in project.iterdir()) == ["shared"]


def test_a_suites_folder_linked_out_of_the_project_is_refused(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    symlink(project / "evals", outside)
    with pytest.raises(SpecError, match="folder: evals/triage leads outside the project"):
        create_suite(SuiteSpec.from_dict(BODY), project)
    assert list(outside.iterdir()) == []


def test_locate_suite_finds_a_suite_by_name(suite_path: Path) -> None:
    root = suite_path.parents[2]
    assert locate_suite(root, "triage") == suite_path


@pytest.mark.parametrize(
    ("name", "folder", "problem"),
    [
        ("..", "evals", "name: use letters, digits"),
        ("a/b", "evals", "name: use letters, digits"),
        ("triage", "../evals", "folder: use a folder inside the project"),
        ("missing", "evals", "name: no suite evals/missing"),
    ],
)
def test_locate_suite_refuses_names_that_leave_the_project(
    suite_path: Path, name: str, folder: str, problem: str
) -> None:
    root = suite_path.parents[2]
    with pytest.raises(SpecError) as caught:
        locate_suite(root, name, folder)
    assert caught.value.problems[0].startswith(problem)


def test_locate_suite_follows_symlinks_before_trusting_a_name(tmp_path: Path) -> None:
    project, outside = tmp_path / "project", tmp_path / "outside"
    (project / "evals").mkdir(parents=True)
    create_suite(SuiteSpec.from_dict(BODY), outside)
    symlink(project / "evals" / "triage", outside / "evals" / "triage")
    with pytest.raises(SpecError, match="name: evals/triage leads outside the project"):
        locate_suite(project, "triage")
