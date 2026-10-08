"""Writing cases into suite files: TOML rendering, layout, and the rollback."""

import tomllib
from pathlib import Path

import pytest

from validia.suites.authoring import append_case, case_toml, suite_toml, toml_value, write_suite
from validia.suites.expect import Label
from validia.suites.suite import Grade, SuiteError


@pytest.mark.parametrize(
    ("value", "toml"),
    [
        (True, "true"),
        (False, "false"),
        (3, "3"),
        (2.5, "2.5"),
        ("plain", '"plain"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("café", '"café"'),
        (r"\bno\b", r"'\bno\b'"),
        ("it's \\d", '"it\'s \\\\d"'),
        (["a", 1], '["a", 1]'),
        ({}, "{}"),
        ({"tool": "x", "args": {"id": "1"}}, '{ tool = "x", args = { id = "1" } }'),
        ({"two words": 1}, '{ "two words" = 1 }'),
    ],
)
def test_values_render_as_toml_that_reads_back(value: object, toml: str) -> None:
    assert toml_value(value) == toml
    assert tomllib.loads(f"v = {toml}")["v"] == value


def test_null_has_no_toml_form() -> None:
    with pytest.raises(ValueError, match="TOML cannot hold None"):
        toml_value({"product": None})


def test_a_case_is_laid_out_like_the_examples() -> None:
    block = case_toml("refund", "I want my money back", "normal", ("normal", "billing"))
    assert block == (
        "[[cases]]\n"
        'id = "refund"\n'
        'tags = ["normal", "billing"]\n'
        'input = "I want my money back"\n'
        'expected = "normal"\n'
    )
    assert "tags" not in case_toml("x", "y", "z")


SUITE = 'prompt = "prompt.md"\n[grade]\ntype = "label"\nlabels = ["yes", "no"]\n'


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    (tmp_path / "prompt.md").write_text("Answer yes or no.\n", encoding="utf-8")
    return tmp_path / "suite.toml"


@pytest.mark.parametrize("ending", ["", "\n", "\n\n"])
def test_appending_keeps_one_blank_line_between_blocks(suite: Path, ending: str) -> None:
    suite.write_text(SUITE.rstrip("\n") + ending, encoding="utf-8")
    loaded = append_case(suite, case_toml("one", "Is water wet?", "yes"))
    assert loaded.cases[0].expected == Label("yes")
    text = suite.read_text(encoding="utf-8")
    assert '"no"]\n\n[[cases]]\n' in text


def test_a_case_that_breaks_the_suite_is_rolled_back(suite: Path) -> None:
    suite.write_text(SUITE, encoding="utf-8")
    with pytest.raises(SuiteError, match="not one of the labels"):
        append_case(suite, case_toml("one", "Is water wet?", "maybe"))
    assert suite.read_text(encoding="utf-8") == SUITE


def test_a_new_suite_file_reads_back() -> None:
    text = suite_toml(
        grade=Grade("json", required=("a",)),
        prompt="prompt.md",
        cases=[case_toml("one", "x", {"a": 1})],
        run="evals/x/suite.toml",
        description="What it measures",
        tools="tools.json",
    )
    data = tomllib.loads(text)
    assert data["grade"] == {"type": "json", "required": ["a"]}
    assert data["tools"] == "tools.json"
    assert data["cases"][0]["expected"] == {"a": 1}
    assert "#   validia run evals/x/suite.toml --model MODEL --dry-run" in text


def test_a_new_suite_that_does_not_load_leaves_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "evals/broken"
    text = suite_toml(
        grade=Grade("label", labels=("yes", "no")),
        prompt="prompt.md",
        cases=[case_toml("one", "x", "maybe")],
        run="suite.toml",
    )
    with pytest.raises(SuiteError, match="not one of the labels"):
        write_suite(folder, {"suite.toml": text, "prompt.md": "Answer.\n"})
    assert not folder.exists()
    assert (tmp_path / "evals").exists()


def test_an_existing_folder_is_kept_on_failure(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SuiteError):
        write_suite(tmp_path, {"suite.toml": "prompt = 1\n"})
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]
