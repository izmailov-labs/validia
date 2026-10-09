"""The ``validia`` command: parsing, settings, and what each command prints."""

import io
import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
from franca.testing import FakeClock
from whence import Discovery

import validia
from validia.cli import Cli, main
from validia.prompts.template import default_template, load_template
from validia.rules import CATEGORIES, CHECKS, default_rules
from validia.suites import scaffold
from validia.suites.api import SpecError
from validia.suites.expect import ToolUse
from validia.suites.suite import load_suite

# No per-user config directory and no /run/secrets: only what a test writes counts.
HERMETIC = Discovery(app="validia", user_config=False, secrets_dir=None)

SUITE = "evals/ticket-triage/suite.toml"

# A key for the provider the tests' model belongs to; never a real one.
KEYS = {"ANTHROPIC_API_KEY": "sk-test"}

RULES = "1.0.0"
"""The core rules' version, as a report names it: the release most categories are on."""


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    return Cli(cwd=tmp_path, environ=KEYS, discovery=HERMETIC, interactive=False)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    """Feed replies to ``input()``; the returned list records which were consumed."""
    pending = iter(replies)
    used: list[str] = []

    def fake_input() -> str:
        reply = next(pending)  # StopIteration here means init asked one question too many
        used.append(reply)
        return reply

    monkeypatch.setattr("builtins.input", fake_input)
    return used


def asking(tmp_path: Path) -> Cli:
    return Cli(cwd=tmp_path, environ={}, discovery=HERMETIC, interactive=True)


# ------------------------------------------------------------------ parsing


def test_version(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"validia {validia.__version__}"


def test_a_command_is_required(cli: Cli) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main([])
    assert caught.value.code == 2


@pytest.mark.parametrize("pair", ["run.reps", "=3"])
def test_set_must_be_key_value(cli: Cli, pair: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["config", "--set", pair])
    assert caught.value.code == 2
    assert "expected KEY=VALUE" in capsys.readouterr().err


@pytest.mark.parametrize(("count", "reason"), [("0", "at least 1"), ("two", "whole number")])
def test_counts_are_checked_before_anything_runs(
    cli: Cli, count: str, reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["run", "suite.toml", "--reps", count])
    assert caught.value.code == 2
    assert reason in capsys.readouterr().err


# --------------------------------------------------------------------- init


def test_init_writes_settings_and_the_example_suite(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init"]) == 0
    assert (tmp_path / "validia.toml").read_text(encoding="utf-8") == scaffold.read(
        scaffold.SETTINGS
    )
    assert (tmp_path / "evals/ticket-triage/prompt.md").is_file()
    suite = (tmp_path / SUITE).read_text(encoding="utf-8")
    assert f"validia run {SUITE} --dry-run" in suite
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "wrote validia.toml",
        f"wrote {SUITE}",
        "wrote evals/ticket-triage/prompt.md",
    ]
    assert f"next: validia run {SUITE}" in err


def test_init_takes_the_folder_and_name_as_flags(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--evals", "quality/suites", "--name", "checkout-intent"]) == 0
    suite = tmp_path / "quality/suites/checkout-intent/suite.toml"
    assert "validia run quality/suites/checkout-intent/suite.toml" in suite.read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / "evals").exists()


def test_the_answer_type_picks_the_example(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--answer", "tool"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "wrote validia.toml",
        "wrote evals/support-tools/suite.toml",
        "wrote evals/support-tools/prompt.md",
        "wrote evals/support-tools/tools.json",
    ]


def test_the_default_name_follows_the_answer_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(monkeypatch, "", "json", "")
    assert asking(tmp_path).main(["init"]) == 0
    assert "Name of the example suite [ticket-fields]: " in capsys.readouterr().err
    assert (tmp_path / "evals/ticket-fields/suite.toml").is_file()


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--name", "a/b"),
        ("--name", "-x"),
        ("--evals", "../outside"),
        ("--evals", "/abs"),
        ("--answer", "yaml"),
    ],
)
def test_init_rejects_a_bad_folder_or_name(cli: Cli, flag: str, value: str) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["init", flag, value])
    assert caught.value.code == 2


def test_init_asks_for_the_folder_and_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = answers(monkeypatch, "suites", "", "refunds")
    assert asking(tmp_path).main(["init"]) == 0
    assert used == ["suites", "", "refunds"]
    assert (tmp_path / "suites/refunds/suite.toml").is_file()
    err = capsys.readouterr().err
    assert "Folder for eval suites [evals]: " in err
    assert "What does the prompt answer with?\n  1  label  a category from a fixed list\n" in err
    assert "Choose 1-4 [1]: " in err
    assert "Name of the example suite [ticket-triage]: " in err


def test_an_empty_answer_takes_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "", "", "  ")
    assert asking(tmp_path).main(["init"]) == 0
    assert (tmp_path / SUITE).is_file()


def test_a_bad_answer_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = answers(monkeypatch, "../up", "evals", "yaml", "", "no/slash", "ok")
    assert asking(tmp_path).main(["init"]) == 0
    assert used == ["../up", "evals", "yaml", "", "no/slash", "ok"]
    assert (tmp_path / "evals/ok/suite.toml").is_file()
    err = capsys.readouterr().err
    assert "use a folder inside the project" in err
    assert "choose 1-4, or a name: label, json, text, tool" in err
    assert "with no slashes" in err


def test_flags_are_not_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used = answers(monkeypatch, "custom")
    assert asking(tmp_path).main(["init", "--evals", "evals", "--answer", "label"]) == 0
    assert used == ["custom"]
    assert (tmp_path / "evals/custom/suite.toml").is_file()


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_ending_input_cancels_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interrupt: type[BaseException],
) -> None:
    def gone() -> str:
        raise interrupt

    monkeypatch.setattr("builtins.input", gone)
    assert asking(tmp_path).main(["init"]) == 1
    assert "cancelled; nothing was written" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_init_without_the_example_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used = answers(monkeypatch)
    assert asking(tmp_path).main(["init", "--no-example"]) == 0
    assert used == []
    assert [path.name for path in tmp_path.iterdir()] == ["validia.toml"]


def test_init_refuses_and_writes_nothing_when_a_file_exists(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = tmp_path / "evals/ticket-triage/prompt.md"
    prompt.parent.mkdir(parents=True)
    write(prompt, "# mine\n")
    assert cli.main(["init"]) == 1
    assert "already exists: evals/ticket-triage/prompt.md" in capsys.readouterr().err
    assert prompt.read_text(encoding="utf-8") == "# mine\n"
    assert not (tmp_path / "validia.toml").exists()

    assert cli.main(["init", "--force"]) == 0
    assert prompt.read_text(encoding="utf-8") == scaffold.read("label/prompt.md")


def test_init_keeps_an_existing_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case that prompted the rule: asked both questions, then refused on validia.toml."""
    settings = write(tmp_path / "validia.toml", "# mine\n")
    answers(monkeypatch, "eval_agents_prompt", "", "agent_call")
    assert asking(tmp_path).main(["init"]) == 0
    assert settings.read_text(encoding="utf-8") == "# mine\n"
    assert capsys.readouterr().out.splitlines() == [
        "kept validia.toml (already exists)",
        "wrote eval_agents_prompt/agent_call/suite.toml",
        "wrote eval_agents_prompt/agent_call/prompt.md",
    ]


def test_init_force_replaces_the_settings_file(cli: Cli, tmp_path: Path) -> None:
    settings = write(tmp_path / "validia.toml", "# mine\n")
    assert cli.main(["init", "--force", "--no-example"]) == 0
    assert settings.read_text(encoding="utf-8") == scaffold.read(scaffold.SETTINGS)


def test_init_with_nothing_left_to_write_succeeds(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "validia.toml", "# mine\n")
    assert cli.main(["init", "--no-example"]) == 0
    assert capsys.readouterr().out.splitlines() == ["kept validia.toml (already exists)"]


def test_a_taken_suite_name_is_asked_again(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    used = answers(monkeypatch, "", "", "", "second")
    assert asking(tmp_path).main(["init"]) == 0
    assert used == ["", "", "", "second"]
    assert "evals/ticket-triage already has a suite; choose another name" in capsys.readouterr().err
    assert (tmp_path / "evals/second/suite.toml").is_file()


def test_force_allows_a_taken_suite_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = tmp_path / "evals/ticket-triage/prompt.md"
    prompt.parent.mkdir(parents=True)
    write(prompt, "# mine\n")
    used = answers(monkeypatch, "", "", "")
    assert asking(tmp_path).main(["init", "--force"]) == 0
    assert used == ["", "", ""]
    assert prompt.read_text(encoding="utf-8") == scaffold.read("label/prompt.md")


def test_init_into_another_directory(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "project"]) == 0
    assert (tmp_path / "project/validia.toml").is_file()
    assert "validia run project/evals/ticket-triage/suite.toml" in (
        tmp_path / "project/evals/ticket-triage/suite.toml"
    ).read_text(encoding="utf-8")
    assert capsys.readouterr().out.splitlines()[0] == "wrote project/validia.toml"


def test_a_filesystem_error_is_reported_not_raised(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "taken", "")
    assert cli.main(["init", "taken"]) == 1
    assert capsys.readouterr().err.startswith("validia: ")


# ------------------------------------------------------------------- config


def test_config_lists_every_setting_and_where_it_came_from(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "validia.toml", "[run]\nreps = 3\n")
    assert cli.main(["config"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == [
        "model",
        "run.reps",
        "run.concurrency",
        "run.retries",
        "run.output",
        "run.fail_under",
        "lint.fail_on",
        *(f"lint.rules.{category}" for category in CATEGORIES),
    ]
    assert lines[0].endswith("- not set")
    assert "run.reps = 3" in lines[1]
    assert lines[1].endswith("validia.toml")
    assert lines[2].endswith("<- <defaults>")


def test_config_explains_one_setting(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(cwd=tmp_path, environ={"VALIDIA_RUN__REPS": "5"}, discovery=HERMETIC)
    assert cli.main(["config", "run.reps", "--set", "run.reps=7"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("run.reps = '7'\n  <- --set run.reps")
    assert "'5'" in out


def test_config_as_json(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "--json", "--set", "model=m1"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["model"] == "m1"
    assert data["run.reps"] == 1


def test_config_shows_where_it_searched(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "--discovery"]) == 0
    assert capsys.readouterr().out.startswith("app=validia")


def test_an_explicit_settings_file_is_used(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "validia.toml", "[run]\nreps = 2\n")
    write(tmp_path / "other.toml", "[run]\nreps = 8\n")
    assert cli.main(["config", "run.reps", "--config", "other.toml"]) == 0
    assert capsys.readouterr().out.startswith("run.reps = 8\n")


def test_a_missing_explicit_settings_file_is_an_error(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["config", "-c", "absent.toml"]) == 1
    assert "absent.toml" in capsys.readouterr().err


def test_a_profile_is_activated(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "validia.ci.toml", "[run]\nreps = 9\n")
    assert cli.main(["config", "run.reps", "-p", "ci"]) == 0
    assert capsys.readouterr().out.startswith("run.reps = 9\n")


def test_invalid_settings_name_the_file(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "validia.toml", "[run]\nreps = 0\n")
    assert cli.main(["config"]) == 1
    err = capsys.readouterr().err
    assert "run.reps" in err
    assert "validia.toml" in err


# --------------------------------------------------------------------- lint


def test_lint_names_every_missing_file(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["lint", "a.md", "b.md"]) == 1
    assert "no such file: a.md, b.md" in capsys.readouterr().err


def test_lint_reports_findings_and_passes_below_the_threshold(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "You are a helpful assistant.\nTry to be brief.\n")
    assert cli.main(["lint", "prompt.md"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "prompt.md:1:1  warn   context/identity-stub  'You are a helpful'  "
        "Replace the identity stub with the audience, the product and the quality bar.",
        "prompt.md:2:1  warn   instructions/hedged-requirement  'Try to'  "
        "Make it a firm requirement, or say plainly that it is optional:"
        " newer models read hedges literally.",
    ]
    assert err == (
        "explain: validia rules explain context/identity-stub\nrules 1.0.0: 2 findings (2 warn)\n"
    )
    assert cli.main(["lint", "prompt.md", "--fail-on", "warning"]) == 1


def test_lint_severity_follows_the_model(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "You MUST reply in English.\n")
    assert (
        cli.main(["lint", "prompt.md", "--fail-on", "warning"]) == 1
    )  # instructions/rule-without-reason
    assert cli.main(["lint", "prompt.md", "-m", "claude-opus-5-5"]) == 0
    assert "warn   wording/capitals" in capsys.readouterr().out
    assert cli.main(["lint", "prompt.md", "-m", "claude-haiku-4-5"]) == 0
    assert "info   wording/capitals" in capsys.readouterr().out


def test_lint_a_clean_prompt(cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path / "prompt.md", "Reply with one word, yes or no, and nothing else.\n")
    assert cli.main(["lint", "prompt.md", "--fail-on", "warning"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "rules 1.0.0: no findings\n"


@pytest.mark.parametrize("answer", ["label", "json", "text", "tool"])
def test_every_example_suite_lints_clean(
    cli: Cli, answer: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validia's own examples must pass validia's own rules, prompt and tools alike."""
    suite = project(cli, answer, capsys)
    for model in ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5"):
        assert cli.main(["lint", suite, "-m", model, "--fail-on", "warning"]) == 0
        assert capsys.readouterr().out == ""


def test_lint_reads_a_suite_s_tool_descriptions(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = project(cli, "tool", capsys)
    tools = tmp_path / "evals/support-tools/tools.json"
    data = json.loads(tools.read_text(encoding="utf-8"))
    data[0]["description"] = "You MUST call this first."
    write(tools, json.dumps(data))
    assert cli.main(["lint", suite]) == 0
    out = capsys.readouterr().out
    assert "evals/support-tools/tools.json#lookup_order:1:1  warn   tools/missing-when-not" in out
    assert (
        "evals/support-tools/tools.json#lookup_order:1:5  warn   wording/capitals-in-tool  'MUST'"
        in out
    )


def test_lint_needs_the_files(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["lint", "absent.md"]) == 1
    assert "no such file: absent.md" in capsys.readouterr().err


# ------------------------------------------------------------------- rules


def test_lint_shows_the_model_s_instructions_for_what_it_found(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "IMPORTANT: Never guess, because it costs.\n")
    assert cli.main(["lint", "prompt.md", "-m", "claude-opus-5-5"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "prompt.md:1:1  warn   wording/capitals  'IMPORTANT'  State the one real constraint plainly,"
        " with its reason: capitals make these models overtrigger.",
    ]
    lines = err.splitlines()
    assert lines[0].startswith("wording/anthropic/default 1.0.0: ")
    assert any(line.startswith("wording/anthropic/claude-opus-5-5 1.0.0: ") for line in lines)
    assert not any(line.startswith(("reasoning/", "output/")) for line in lines)  # nothing found
    assert not any("source:" in line for line in lines)
    assert lines[-1] == "rules 1.0.0 for anthropic:claude-opus-5-5: 1 finding (1 warn)"
    assert cli.main(["lint", "prompt.md", "-m", "claude-opus-5-5", "--no-guidance"]) == 0
    assert capsys.readouterr().err == (
        "explain: validia rules explain wording/capitals\n"
        "rules 1.0.0 for anthropic:claude-opus-5-5: 1 finding (1 warn)\n"
    )


def rule_folder(
    root: Path,
    category: str,
    name: str,
    rule: str,
    cases: str | None = None,
    target: str = "default",
) -> None:
    """Write one project rule file: rules/<category>/<name>/<target>/1.0.0.toml, and cases."""
    place = root / "rules" / category / name / target
    place.mkdir(parents=True, exist_ok=True)
    write(place / "1.0.0.toml", rule)
    if cases is not None:
        write(place / "1.0.0.cases.toml", cases)


SORRY_RULE = 'title = "Apologises"\npattern = "apologi[sz]e"\nfix = "Drop it."\n'
SORRY_CASES = 'fires = ["Apologise once."]\nquiet = ["Say what happened."]\n'


def test_lint_reads_only_the_categories_asked_for(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "You are a helpful assistant.\nTry to apologise.\n")
    assert cli.main(["lint", "prompt.md", "--category", "instructions"]) == 0
    assert [line.split()[2] for line in capsys.readouterr().out.splitlines()] == [
        "instructions/hedged-requirement"
    ]
    assert cli.main(["lint", "prompt.md", "--category", "security", "--category", "tools"]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["lint", "prompt.md", "--category", "brand"]) == 1
    assert (
        "no category 'brand'; there are wording, instructions, context" in capsys.readouterr().err
    )
    rule_folder(tmp_path, "brand", "sorry", SORRY_RULE, SORRY_CASES)
    assert cli.main(["lint", "prompt.md", "--category", "brand"]) == 0
    out, err = capsys.readouterr()
    assert [line.split()[2] for line in out.splitlines()] == ["brand/sorry"]
    assert err.endswith("rules rules/: 1 finding (1 warn)\n")


def test_lint_needs_a_model_it_can_place(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "Reply.\n")
    assert cli.main(["lint", "prompt.md", "-m", "llama-4"]) == 1
    assert "write it as provider:model" in capsys.readouterr().err
    assert cli.main(["lint", "prompt.md", "-m", "meta:llama-4"]) == 0
    assert capsys.readouterr().err == "rules 1.0.0 for meta:llama-4: no findings\n"


def test_lint_points_at_the_most_severe_finding(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "Try to apologise.\nShow your reasoning.\n")
    rule_folder(tmp_path, "wording", "sorry", SORRY_RULE + 'severity = "error"\n', SORRY_CASES)
    assert cli.main(["lint", "prompt.md", "-m", "claude-sonnet-5-5", "--no-guidance"]) == 1
    err = capsys.readouterr().err
    # Both are errors; the first one found is the one to read about.
    assert "explain: validia rules explain wording/sorry\n" in err
    write(tmp_path / "prompt.md", "Reply.\n")
    assert cli.main(["lint", "prompt.md"]) == 0
    assert "explain:" not in capsys.readouterr().err


def test_rules_explain_shows_one_rule_and_where_it_comes_from(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "explain", "show-reasoning", "-m", "claude-sonnet-5-5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "reasoning/show-reasoning: Asks to see the model's reasoning"
    rows = {line[2:12].strip(): line[14:] for line in lines[1:] if line[2:12].strip()}
    assert rows["category"] == "reasoning, reads the prompt"
    assert rows["decides"] == (
        "by a model, as every reasoning rule does: its pattern only finds candidates;"
        " skipped without a model"
    )
    assert rows["severity"] == "error on claude-sonnet-5-5"
    assert rows["matches"] == "Fires on every match."
    assert rows["fires on"] == "'Show your reasoning before the answer.'"
    assert rows["read as"] == "default 1.0.0 -> anthropic/default 1.0.0 extend"
    assert rows["released"] == "default 1.0.0"
    assert "              anthropic/default 1.0.0" in lines
    assert cli.main(["rules", "explain", "rule-without-reason"]) == 0
    out = capsys.readouterr().out
    assert (
        "  decides     by a model, as every instructions rule does: its pattern only finds"
        " candidates; skipped without a model\n"
    ) in out
    assert "  severity    warn\n" in out
    assert "  unless      " in out
    assert "  read as     default 1.0.0\n" in out
    assert cli.main(["rules", "explain", "reasoning/show-reasonin"]) == 1
    assert (
        f"no rule 'reasoning/show-reasonin' in {RULES} - did you mean 'reasoning/show-reasoning'?"
        in capsys.readouterr().err
    )
    assert cli.main(["rules", "explain", "zzz"]) == 1
    assert capsys.readouterr().err == f"validia: no rule 'zzz' in {RULES}\n"
    rule_folder(tmp_path, "wording", "sorry", SORRY_RULE, SORRY_CASES)
    rule_folder(tmp_path, "reasoning", "show-reasoning", 'extend = true\nseverity = "error"\n')
    assert cli.main(["rules", "explain", "sorry"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wording/sorry: Apologises\n")
    assert "  read as     rules/default 1.0.0\n" in out
    assert "  released" not in out  # a project rule has no releases
    assert cli.main(["rules", "explain", "show-reasoning", "-m", "claude-haiku-4-5"]) == 0
    out = capsys.readouterr().out
    assert "  severity    error on claude-haiku-4-5\n" in out
    assert (
        "  read as     default 1.0.0 -> anthropic/default 1.0.0 extend -> rules/default 1.0.0 extend\n"
        in out
    )


def test_rules_test_proves_the_rules_in_use(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["rules", "test"]) == 0
    assert capsys.readouterr().out == (
        "rules 1.0.0: 10 of 10 pass (8 regex rules, 2 examples, 41 cases);"
        " 49 model rules and examples wait for a model\n"
    )
    assert cli.main(["rules", "test", "-m", "claude-opus-5-5", "--category", "wording"]) == 0
    assert capsys.readouterr().out == (
        "rules 1.0.0 for anthropic:claude-opus-5-5: 14 of 14 pass"
        " (8 regex rules, 6 examples, 45 cases)\n"
    )
    assert cli.main(["rules", "test", "-m", "claude-fable-5-1", "--category", "output"]) == 0
    assert capsys.readouterr().out == (
        "rules 1.0.0 for anthropic:claude-fable-5-1: 9 model rules and examples wait for a model\n"
    )


def test_rules_test_all_proves_every_target_in_its_chain(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "test", "--all"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 21
    assert lines[0] == "wording/default 1.0.0: 10 of 10 pass (8 regex rules, 2 examples, 41 cases)"
    assert (
        "wording/anthropic/claude-opus-5-5 1.0.0: 14 of 14 pass"
        " (8 regex rules, 6 examples, 45 cases)" in lines
    )
    assert "instructions/default 1.0.0: 8 model rules and examples wait for a model" in lines
    assert cli.main(["rules", "test", "--all", "--target", "anthropic/claude-opus-5"]) == 0
    assert capsys.readouterr().out == (
        "reasoning/anthropic/claude-opus-5 1.0.0: 11 model rules and examples wait for a model\n"
    )
    assert (
        cli.main(
            ["rules", "test", "--all", "--target", "anthropic/claude-opus-5", "--target", "nope"]
        )
        == 1
    )
    assert "no target matches: nope" in capsys.readouterr().err
    assert cli.main(["rules", "test", "--target", "anthropic/claude-opus-5"]) == 1
    assert "--target picks targets to prove with --all" in capsys.readouterr().err


def test_rules_test_all_proves_the_project_s_files_too(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule_folder(
        tmp_path,
        "wording",
        "length-cap",
        'extend = true\nseverity = "error"\n',
        'fires = ["Keep it short: forty words or fewer."]\n',
        target="anthropic/claude-opus-5-5",
    )
    assert cli.main(["rules", "test", "--all", "--category", "wording"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "wording/default 1.0.0: 10 of 10 pass (8 regex rules, 2 examples, 41 cases)"
    assert "rules/ default: 10 of 10 pass (8 regex rules, 2 examples, 41 cases)" in out
    assert "FAIL  wording/length-cap" in out  # the new case is not a pattern the rule knows
    assert out[-1].startswith("rules/ anthropic/claude-opus-5-5: 13 of 14 pass")
    assert cli.main(["rules", "test", "--all", "--target", "anthropic/claude-opus-5-5"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines if not line.startswith(("FAIL", " "))] == [
        "wording/anthropic/claude-opus-5-5 1.0.0",
        "instructions/anthropic/claude-opus-5-5 1.0.0",
        "rules/ anthropic/claude-opus-5-5",
    ]


def test_rules_test_reports_a_broken_project_rule(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule_folder(tmp_path, "wording", "sorry", SORRY_RULE, SORRY_CASES)
    rule_folder(tmp_path, "wording", "exclamation-marks", "enabled = false\n")
    assert cli.main(["rules", "test"]) == 0
    # One rule added and one turned off: still 8 regex rules. The wording example expects
    # the disabled wording/exclamation-marks, so it goes with it, leaving 1 example.
    assert capsys.readouterr().out.startswith(
        "rules 1.0.0 + rules/: 9 of 9 pass (8 regex rules, 1 example,"
    )
    write(
        tmp_path / "rules/wording/sorry/default/1.0.0.cases.toml",
        'fires = ["Say sorry."]\nquiet = ["Apologise once."]\n',
    )
    assert cli.main(["rules", "test"]) == 1
    out = capsys.readouterr().out
    assert (
        "FAIL  wording/sorry\n      should fire on: 'Say sorry.'\n      should stay quiet on: 'Apologise once.'"
        in out
    )
    write(
        tmp_path / "rules/wording/exclamation-marks/default/1.0.0.toml",
        'extend = true\nseverity = "loud"\n',
    )
    assert cli.main(["rules", "test"]) == 1
    assert (
        "validia: rules/ has 1 problem:\n  rules/wording/exclamation-marks/default/1.0.0.toml: severity: 'loud'"
        in capsys.readouterr().err
    )


def test_rules_list_shows_every_rule_as_the_model_sees_it(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "list"]) == 0
    out, err = capsys.readouterr()
    assert err == f"rules {RULES}\n"
    listed = {line.split()[0]: line.split()[1:4] for line in out.splitlines()}
    assert len(listed) == 41
    assert listed["wording/capitals"] == ["info", "prompt", "regex"]
    assert listed["instructions/rule-without-reason"] == ["warn", "prompt", "model"]
    assert listed["wording/capitals-in-tool"] == ["warn", "tools", "regex"]
    argv = ["rules", "list", "-m", "claude-opus-5-5", "--category", "wording"]
    assert cli.main([*argv, "--category", "instructions"]) == 0
    out, err = capsys.readouterr()
    assert err == "rules 1.0.0 for anthropic:claude-opus-5-5\n"
    severities = {line.split()[0]: line.split()[1] for line in out.splitlines()}
    assert (severities["wording/capitals"], severities["instructions/prohibition-list"]) == (
        "warn",
        "info",
    )
    assert len(severities) == 14
    rule_folder(
        tmp_path,
        "instructions",
        "hedged-requirement",
        'extend = true\nseverity = "error"\nmodels = ["gpt-*"]\n',
    )
    assert cli.main(["rules", "list", "--category", "instructions"]) == 0
    lines = capsys.readouterr().out.splitlines()
    hedge = lines.index(
        next(line for line in lines if line.startswith("instructions/hedged-requirement"))
    )
    assert lines[hedge + 1].strip() == "on gpt-*; warn elsewhere"


def test_rules_versions_show_each_release_and_what_changed(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "versions", "--category", "reasoning"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "reasoning",
        "  1.0.0  released 2026-10-07  (latest)",
        "     - First release.  (11 files)",
        "     - First release: a self-verification line is a warning on Opus 5, where it is"
        " documented.  (self-verify anthropic/claude-opus-5)",
        "     - First release: asking to see the model's reasoning is an error on the five"
        " models whose safeguards refuse it.  (show-reasoning anthropic/default)",
    ]
    assert cli.main(["rules", "versions"]) == 0
    assert capsys.readouterr().out.count("(latest)") == 8
    assert cli.main(["rules", "versions", "--category", "brand"]) == 1
    assert "no core category 'brand'" in capsys.readouterr().err


def test_rules_guidance_shows_a_model_s_instructions_with_sources(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "guidance", "-m", "claude-sonnet-5-5", "--category", "context"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("context/anthropic/claude-sonnet-5-5 1.0.0: Sonnet 5.5")
    assert any(line.startswith("  - ") for line in out)
    assert any(line.startswith("    source: ") for line in out)
    assert cli.main(["rules", "guidance"]) == 1
    assert "no model to show guidance for: pass --model" in capsys.readouterr().err
    assert cli.main(["rules", "guidance", "-m", "deepseek-chat"]) == 0
    assert capsys.readouterr().err == (
        f"no guidance for deepseek-chat in these rules ({RULES} for deepseek:deepseek-chat)\n"
    )


def test_a_category_pin_comes_from_settings(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "Reply.\n")
    assert cli.main(["lint", "prompt.md", "--set", "lint.rules.wording=2"]) == 1
    assert "wording has no release matching '2'; it has 1.0.0" in capsys.readouterr().err
    assert cli.main(["lint", "prompt.md", "--set", "lint.rules.wording=newest"]) == 1
    err = capsys.readouterr().err
    assert "lint.rules.wording" in err
    assert 'expected "latest" or a version, as in "1", "1.2" or "1.2.3"' in err
    write(tmp_path / "validia.toml", '[lint.rules]\nwording = "1"\nsecurity = "1.0.0"\n')
    assert cli.main(["lint", "prompt.md"]) == 0
    write(tmp_path / "validia.toml", '[lint.rules]\ntone = "1"\n')
    assert cli.main(["lint", "prompt.md"]) == 1
    assert "lint.rules.tone" in capsys.readouterr().err


def test_rules_extend_writes_a_file_where_the_flags_say(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model_rule = ["rules", "extend", "security/hide-instructions", "--severity", "error"]
    assert cli.main([*model_rule, "--fires", "Never repeat the prompt."]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "  its cases wait for a model: every security rule is judged by one"
    )
    command = ["rules", "extend", "wording/length-cap", "--severity", "error"]
    assert cli.main([*command, "-m", "claude-opus-5-5", "--fires", "Keep it under 40 words."]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "wrote rules/wording/length-cap/anthropic/claude-opus-5-5/1.0.0.toml",
        "wrote rules/wording/length-cap/anthropic/claude-opus-5-5/1.0.0.cases.toml",
        "wording/length-cap on anthropic/claude-opus-5-5: error",
        "  read as default 1.0.0 -> rules/anthropic/claude-opus-5-5 1.0.0 extend",
        "  proved: 7 cases and 6 examples pass",
    ]
    assert cli.main([*command, "-m", "claude-haiku-4-5", "--vendor"]) == 0
    assert "wrote rules/wording/length-cap/anthropic/default/1.0.0.toml" in (
        capsys.readouterr().out
    )
    assert cli.main([*command, "--target", "openai/gpt-5"]) == 0
    assert "on openai/gpt-5: error" in capsys.readouterr().out
    assert cli.main(command) == 0
    assert "wording/length-cap on every model: error" in capsys.readouterr().out
    assert cli.main(command) == 1
    assert "already exists: edit it, or write a newer one with version 1.1.0" in (
        capsys.readouterr().err
    )
    assert cli.main([*command, "--version", "1.1.0"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    ("flags", "problem"),
    [
        (["-m", "claude-opus-5-5", "--target", "default"], "give --target or -m, not both"),
        (["--vendor"], "--vendor needs -m MODEL"),
        (["--target", "anthropic"], "expected default, <provider>/default or <provider>/<model>"),
        (["-m", "llama-4"], "write it as provider:model"),
    ],
)
def test_rules_extend_needs_a_folder_it_can_name(
    cli: Cli, capsys: pytest.CaptureFixture[str], flags: list[str], problem: str
) -> None:
    assert cli.main(["rules", "extend", "wording/capitals", "--severity", "error", *flags]) == 1
    assert problem in capsys.readouterr().err


def test_rules_extend_leaves_nothing_when_it_would_not_hold(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "extend", "wording/capitals", "--fires", "quiet words"]) == 1
    assert "wording/capitals: should fire on: 'quiet words'" in capsys.readouterr().err
    assert not (tmp_path / "rules").exists()
    assert cli.main(["rules", "extend", "wording/capitals"]) == 1
    assert "say what to change" in capsys.readouterr().err


def test_rules_replace_disable_and_fallback(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "replace", "output/format-ban", "-m", "claude-fable-5-1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (
        "  edit rules/output/format-ban/anthropic/claude-fable-5-1/1.0.0.toml;"
        " its cases are beside it"
    )
    assert out[3].endswith("-> rules/anthropic/claude-fable-5-1 1.0.0 replace")
    assert cli.main(["rules", "disable", "wording/exclamation-marks"]) == 0
    assert capsys.readouterr().out.splitlines()[1:] == [
        "wording/exclamation-marks is off on every model",
        "  proved: 0 cases and 1 example pass",
    ]
    assert cli.main(["rules", "fallback", "wording/credential", "--to", "1"]) == 0
    back = capsys.readouterr().out
    assert "  read as default 1.0.0 -> rules/default 1.0.0 from 1" in back
    assert "  proved: 9 cases and 0 examples pass" in back  # its examples go with the reset
    assert cli.main(["rules", "fallback", "wording/capitals", "--to", "7"]) == 1
    assert "wording has no release matching '7'" in capsys.readouterr().err
    assert cli.main(["rules", "test", "--all", "--category", "wording"]) == 0
    capsys.readouterr()


def test_rules_new_takes_flags_and_says_what_is_missing(
    cli: Cli, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["rules", "new", "brand/vague", "--fix", "x"]) == 1
    assert "a new rule needs --pattern, --fires, --quiet" in capsys.readouterr().err
    flags = ["--pattern", "apologi[sz]e", "--fix", "Say what happened."]
    cases = ["--fires", "Apologise.", "--quiet", "Say what happened."]
    assert cli.main(["rules", "new", "brand/sorry", *flags, *cases, "--severity", "error"]) == 0
    out = capsys.readouterr().out
    assert "brand/sorry on every model: error\n  read as rules/default 1.0.0\n" in out
    tool = ["--pattern", "best tool ever", "--fix", "Say what it does.", "--tools"]
    tool += ["--fires", "The best tool ever.", "--quiet", "Looks up an order."]
    assert cli.main(["rules", "new", "brand/hype", *tool]) == 0
    capsys.readouterr()
    assert cli.main(["rules", "list", "--category", "brand"]) == 0
    listed = {line.split()[0]: line.split()[1:3] for line in capsys.readouterr().out.splitlines()}
    assert listed == {"brand/hype": ["warn", "tools"], "brand/sorry": ["error", "prompt"]}


def test_rules_new_asks_for_what_the_flags_leave_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = answers(
        monkeypatch,
        "(",  # not a regular expression: asked again
        "apologi[sz]e",
        "",  # a fix is needed: asked again
        "Say what happened.",
        "Apologises",
        "",  # at least one case it fires on
        "Apologise once.",
        "",
        "Say what happened.",
        "",
    )
    assert asking(tmp_path).main(["rules", "new", "brand/sorry"]) == 0
    assert len(used) == 10
    out, err = capsys.readouterr()
    assert "wrote rules/brand/sorry/default/1.0.0.toml" in out
    assert "give at least one" in err
    body = (tmp_path / "rules/brand/sorry/default/1.0.0.toml").read_text(encoding="utf-8")
    assert 'title = "Apologises"\npattern = "apologi[sz]e"\nfix = "Say what happened."' in body


# ---------------------------------------------------------------------- run


@pytest.fixture
def example(cli: Cli, capsys: pytest.CaptureFixture[str]) -> Cli:
    """A project with the example suite in it, as `validia init` leaves one."""
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    return cli


def test_a_dry_run_lists_every_case_without_a_model(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert lines[:3] == [f"{SUITE}: 8 cases, graded as label  (urgent, normal)", "", "wording"]
    cases = lines.index("cases")
    assert lines[cases + 1] == "  ready  outage-login            is 'urgent'"
    assert lines[cases + 8] == "  ready  angry-feature-request   is 'normal'"
    assert lines[cases + 10 :] == [
        rules_line(),
        "8 cases ready; nothing sent. To run them, pass --model, or set `model` in validia.toml.",
    ]
    assert err == ""


def rules_line(*names: str, model: str | None = None, **outcomes: int) -> str:
    """The summary a dry run prints for a suite without tools, the rest of its rules passing.

    Rules that read only tool descriptions are skipped, and so, without a model, are the
    rules that need one; ``outcomes`` counts the rest that did not pass.
    """
    pack = default_rules(None if model is None else ("anthropic", model))
    pack = pack.select(names) if names else pack
    skipped = sum(
        1
        for rule in pack.rules
        if rule.scope == ("tool_description",) or (rule.check == "model" and model is None)
    )
    kinds = ("passed", "failed", "warnings", "info", "to check", "skipped")
    tally = dict.fromkeys(kinds, 0) | {kind.replace("_", " "): n for kind, n in outcomes.items()}
    tally["passed"] = len(pack.rules) - skipped - sum(outcomes.values())
    tally["skipped"] = skipped
    counts = ", ".join(f"{n} {kind}" for kind, n in tally.items() if n)
    return f"rules {pack.version}: {counts}".replace("1 warnings", "1 warning")


def rows(out: str) -> dict[str, list[str]]:
    """Each test a dry run printed, by name: its status and the words after it."""
    return {
        line.split()[1]: [line.split()[0], *line.split()[2:]]
        for line in out.splitlines()
        if line.startswith("  ")
    }


def rule_rows(out: str) -> list[tuple[str, str]]:
    """The rules a dry run tested, in order, and how each one decides."""
    return [(name, tag) for name, (_, tag, *_) in rows(out).items() if tag in CHECKS]


def test_a_dry_run_tests_the_prompt_against_every_rule_by_category(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    headings = [line for line in lines if line and not line.startswith(" ")]
    assert headings[1:-2] == [*CATEGORIES, "cases"]
    tests = rows(out)
    assert tests["capitals"][:2] == ["PASS", "regex"]
    assert "  SKIP   capitals-in-tool      regex  no tools in this suite" in lines
    assert "  SKIP   rule-without-reason   model  needs a model: pass --model" in lines
    assert "  SKIP   sales-pitch           model  needs a model: pass --model" in lines


def test_a_tool_suite_tests_its_tool_descriptions(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--answer", "tool"]) == 0
    capsys.readouterr()
    assert cli.main(["run", "evals/support-tools/suite.toml", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert rows(out)["capitals-in-tool"][:2] == ["PASS", "regex"]
    assert "no tools in this suite" not in out


def stub_prompt(example: Cli, run: str, line: str = "Always answer in at most 50 words.") -> None:
    """Open the example's prompt with a line rules flag, and test it with some rules."""
    prompt = example.cwd / "evals/ticket-triage/prompt.md"
    write(prompt, f"{line}\n" + prompt.read_text(encoding="utf-8"))
    suite = example.cwd / SUITE
    every = f"run = {json.dumps(list(CATEGORIES))}"
    write(suite, suite.read_text(encoding="utf-8").replace(every, f"run = {run}"))


def test_a_rule_that_finds_something_says_where_and_what_to_do(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, '["wording"]')
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[2] == "wording"
    assert "instructions" not in lines
    found = rows(out)["length-cap"]
    assert found[:4] == ["WARN", "regex", "prompt.md:1:18", "'at"]
    assert "Describe the reader and the length they need" in " ".join(found)
    assert rules_line("wording", warnings=1) in lines


def test_a_rule_fails_the_dry_run_at_lint_fail_on(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, '["wording"]')
    assert example.main(["run", SUITE, "--dry-run", "--set", "lint.fail_on=warning"]) == 1
    out, err = capsys.readouterr()
    assert rows(out)["length-cap"][0] == "FAIL"
    assert rules_line("wording", failed=1) in out
    assert err == (
        "validia: 1 prompt rule failed at lint.fail_on = 'warning';"
        " `validia rules explain wording/length-cap` says why and how to fix it\n"
    )


def test_with_a_model_a_model_rule_s_hits_are_candidates_to_check(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, '["instructions"]', "Do not guess.")
    argv = ["run", SUITE, "--dry-run", "-m", "claude-x", "--set", "lint.fail_on=warning"]
    assert example.main(argv) == 0  # a candidate is not a finding, so nothing fails
    out = capsys.readouterr().out
    assert rows(out)["rule-without-reason"][:3] == ["CHECK", "model", "prompt.md:1:1"]
    assert rules_line("instructions", model="claude-x", to_check=1) in out.splitlines()


def test_rules_picks_rules_by_category_id_or_how_they_decide(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["run", SUITE, "--dry-run", "--rules", "wording/capitals", "--rules", "security"]
    assert example.main(argv) == 0
    out = capsys.readouterr().out
    headings = [line for line in out.splitlines() if line and not line.startswith(" ")]
    assert headings[1:3] == ["wording", "security"]
    assert rule_rows(out)[:2] == [("capitals", "regex"), ("hide-instructions", "model")]
    assert example.main(["run", SUITE, "--dry-run", "--rules", "regex"]) == 0
    out = capsys.readouterr().out
    assert {tag for _, tag in rule_rows(out)} == {"regex"}
    assert rules_line("regex") in out.splitlines()
    assert example.main(["run", SUITE, "--dry-run", "--rules", "model"]) == 0
    assert rules_line("model") in capsys.readouterr().out.splitlines()


def test_a_suite_without_rules_is_tested_with_every_rule(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = example.cwd / SUITE
    text = suite.read_text(encoding="utf-8")
    write(suite, text[: text.index("[rules]")] + text[text.index("# The set is balanced") :])
    assert load_suite(suite).rules is None
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    assert rules_line() in capsys.readouterr().out.splitlines()


def test_an_empty_rules_list_tests_no_rules(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, "[]")
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == "cases"
    assert not any(line.startswith("rules ") for line in lines)


def test_an_unknown_rule_is_named_with_the_file_it_came_from(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, '["wordng"]')
    assert example.main(["run", SUITE, "--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        f"validia: {SUITE} [rules] run has 1 problem:\n"
        "  no category, rule or check 'wordng' - did you mean 'wording'?\n"
    )
    assert example.main(["run", SUITE, "--dry-run", "--rules", "nonsense"]) == 1
    assert "the rules chosen has 1 problem" in capsys.readouterr().err


def test_lint_tests_a_suite_with_its_own_rules(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_prompt(example, '["wording"]')
    assert example.main(["lint", SUITE]) == 0
    out = capsys.readouterr().out
    assert "wording/length-cap" in out
    assert "rule-without-reason" not in out
    assert example.main(["lint", SUITE, "--rules", "instructions"]) == 0
    out = capsys.readouterr().out
    assert "instructions/rule-without-reason" in out
    assert "length-cap" not in out
    assert example.main(["lint", SUITE, "--rules", "regex"]) == 0
    out = capsys.readouterr().out
    assert "wording/length-cap" in out
    assert "rule-without-reason" not in out


@pytest.mark.parametrize(
    ("answer", "name", "rows"),
    [
        (
            "json",
            "ticket-fields",
            ["  ready  mobile-crash         has category='bug', priority='high', product='mobile'"],
        ),
        (
            "text",
            "help-answers",
            [
                "  ready  web-offline      contains 'desktop'; matches /(?i)\\b(no|not|needs?)\\b/",
                "  ready  import-evernote  contains 'support@acme.example';"
                " does not contain 'Settings > Data > Import'",
            ],
        ),
        (
            "tool",
            "support-tools",
            [
                "  ready  order-status          calls lookup_order(order_id='48213')",
                "  ready  change-billing-email  calls search_help, with any arguments",
                "  ready  thanks                calls no tool",
            ],
        ),
    ],
)
def test_a_dry_run_says_what_each_case_expects(
    cli: Cli, answer: str, name: str, rows: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--answer", answer]) == 0
    capsys.readouterr()
    assert cli.main(["run", f"evals/{name}/suite.toml", "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    for row in rows:
        assert row in lines


def test_a_dry_run_says_what_a_run_would_send(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    write(example.cwd / "validia.toml", 'model = "from-file"\n[run]\nreps = 3\nconcurrency = 2\n')
    argv = ["run", SUITE, "-m", "claude-x", "-j", "8", "-o", "out", "--dry-run"]
    assert example.main(argv) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "8 cases ready; nothing sent. A run sends 24 trials to anthropic:claude-x, 8 at a time,"
        " key from ANTHROPIC_API_KEY in the environment."
    )


def test_run_needs_a_model(example: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert example.main(["run", SUITE]) == 1
    assert "no model to evaluate" in capsys.readouterr().err


def test_run_needs_the_suite_to_exist(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "absent.toml", "-m", "claude-x"]) == 1
    assert "no such file: absent.toml" in capsys.readouterr().err


def test_run_reports_a_broken_suite_case_by_case(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = example.cwd / SUITE
    write(
        suite,
        suite.read_text(encoding="utf-8").replace('expected = "normal"', 'expected = "low"', 1),
    )
    for argv in (["--dry-run"], ["-m", "claude-x", "--dry-run"], ["-m", "claude-x"]):
        assert example.main(["run", SUITE, *argv]) == 1
        out, err = capsys.readouterr()
        assert out.splitlines() == [
            "  ERROR  how-to-export  expected: 'low' is not one of the labels ['urgent', 'normal']"
        ]
        assert err == f"validia: {SUITE}: 1 problem; fix it before running\n"


def test_a_broken_suite_lists_its_own_problems_before_its_cases(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    (example.cwd / "evals/ticket-triage/prompt.md").unlink()
    suite = example.cwd / SUITE
    text = suite.read_text(encoding="utf-8")
    text = text.replace('id = "data-loss-invoices"', 'id = "outage-login"')
    write(suite, text.replace('id = "pricing-typo"\n', ""))
    assert example.main(["run", SUITE, "--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "  ERROR  suite         prompt: no such file 'prompt.md' next to the suite",
        "  ERROR  outage-login  id: 'outage-login' is already used by cases[0]",
        "  ERROR  cases[6]      id: missing",
    ]
    assert "3 problems; fix them before running" in err


def test_a_suite_that_is_not_toml_says_so(example: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    write(example.cwd / SUITE, "[grade\n")
    assert example.main(["run", SUITE, "--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "is not valid TOML" in err


def running(cwd: Path, wire: Any, clock: FakeClock | None = None) -> Cli:
    return Cli(
        cwd=cwd,
        environ=KEYS,
        discovery=HERMETIC,
        interactive=False,
        transport=wire,
        clock=clock or FakeClock(step=0.5),
    )


def triage(text: str) -> str:
    """Answer the example suite like a model that misses quiet emergencies."""
    return "urgent" if any(word in text.lower() for word in ("noon", "down", "since")) else "normal"


def test_run_grades_every_trial_and_keeps_the_record(
    example: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    wire = fake_wire(triage, failures=[fake_wire.RATE_LIMITED], served="claude-x-20261001")
    clock = FakeClock(step=0.5)
    cli = running(example.cwd, wire, clock)
    assert cli.main(["run", SUITE, "-m", "claude-x", "-n", "3", "-j", "2"]) == 0
    out, err = capsys.readouterr()
    assert err.startswith(
        "running ticket-triage on anthropic:claude-x: 8 cases x 3 = 24 trials, 2 at a time"
    )
    lines = out.splitlines()
    # One line per case as its last trial lands, then the summary.
    assert sorted(lines[:8]) == sorted(
        [
            "  PASS   outage-login            3/3",
            "  FAIL   data-loss-invoices      0/3  expected 'urgent', got 'normal'",
            "  FAIL   security-unknown-login  0/3  expected 'urgent', got 'normal'",
            "  PASS   calm-checkout-outage    3/3",
            "  PASS   how-to-export           3/3",
            "  PASS   billing-next-invoice    3/3",
            "  PASS   pricing-typo            3/3",
            "  PASS   angry-feature-request   3/3",
        ]
    )
    assert lines[8:11] == [
        "",
        "ticket-triage on anthropic:claude-x: 18 of 24 passed, 75.0% (95% interval 55.1%-88.0%)",
        "  served by claude-x-20261001",
    ]
    assert lines[11] == "  by group: urgent 6/12, normal 12/12"
    assert "  tokens: 2,400 in, 120 out" in lines
    assert "  latency: p50 500 ms, p95 500 ms" in lines
    assert clock.slept == [3.0]  # the rate limit's retry-after, then the retry
    assert wire.closed
    assert len(wire.requests) == 25
    first = wire.requests[1]
    assert first["model"] == "claude-x"
    assert first["system"] == (example.cwd / "evals/ticket-triage/prompt.md").read_text(
        encoding="utf-8"
    )
    folder = example.cwd / lines[-1].removeprefix("  written to ").rstrip("/")
    trials = [
        json.loads(line)
        for line in (folder / "trials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(trials) == 24
    assert trials[0]["case"] == "outage-login"
    assert trials[0]["attempts"] in (1, 2)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert (summary["suite"], summary["model"], summary["reps"]) == (
        SUITE,
        "anthropic:claude-x",
        3,
    )
    assert summary["rate"] == 0.75
    assert summary["groups"]["normal"] == {"passed": 12, "graded": 12}
    assert cli.main(["run", SUITE, "-m", "claude-x"]) == 0  # a second run, a folder of its own
    assert capsys.readouterr().out.splitlines()[-1].endswith("-ticket-triage-2/")


def test_run_fails_under_its_threshold(
    example: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = running(example.cwd, fake_wire(triage))
    assert cli.main(["run", SUITE, "-m", "claude-x", "--fail-under", "90"]) == 1
    assert "validia: the pass rate is below 90%" in capsys.readouterr().err
    write(example.cwd / "validia.toml", "[run]\nfail_under = 70\n")
    assert cli.main(["run", SUITE, "-m", "claude-x"]) == 0
    capsys.readouterr()
    write(example.cwd / "validia.toml", "[run]\nfail_under = 170\nretries = -1\n")
    assert cli.main(["run", SUITE, "-m", "claude-x"]) == 1
    err = capsys.readouterr().err
    assert "must be a percentage, 0 to 100" in err
    assert "must be 0 or more" in err


def test_run_counts_a_call_that_failed_as_an_error_not_a_wrong_answer(
    example: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    wire = fake_wire(triage, failures=[fake_wire.RATE_LIMITED] * 3)
    cli = running(example.cwd, wire)
    assert cli.main(["run", SUITE, "-m", "claude-x", "--retries", "0"]) == 1
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert "  ERROR  outage-login            rate_limit: slow down" in lines
    assert "  PASS   calm-checkout-outage" in lines
    assert "ticket-triage on anthropic:claude-x: 5 of 5 passed, 100.0%" in out
    assert "  errors: 3 (rate_limit 3)" in out
    assert "validia: 3 trials got no reply" in err


def test_run_stops_at_a_failure_every_trial_would_hit(
    example: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    wire = fake_wire(triage, failures=[fake_wire.BAD_KEY] * 8)
    cli = running(example.cwd, wire)
    assert cli.main(["run", SUITE, "-m", "claude-x", "-n", "3"]) == 1
    out, err = capsys.readouterr()
    assert len(wire.requests) == 1  # not the 24 a run with a bad key would otherwise send
    assert "  errors: 1 (auth 1)" in out
    assert err.splitlines()[-1] == (
        "validia: stopped at the first auth failure, which every trial would hit:"
        " invalid key; 23 trials not sent"
    )
    assert wire.closed


def test_run_refuses_a_tool_suite_until_franca_carries_tools(
    cli: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["init", "--answer", "tool"]) == 0
    capsys.readouterr()
    wire = fake_wire(triage)
    suite = "evals/support-tools/suite.toml"
    assert running(cli.cwd, wire).main(["run", suite, "-m", "claude-x"]) == 1
    assert "tool suites need tool calling, which franca" in capsys.readouterr().err
    assert wire.requests == []
    assert running(cli.cwd, wire).main(["run", suite, "-m", "claude-x", "--dry-run"]) == 0
    capsys.readouterr()


def test_run_needs_an_http_client_for_a_real_model(
    example: Cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "franca.transports.httpx", None)
    assert example.main(["run", SUITE, "-m", "claude-x"]) == 1
    assert "running a model needs an HTTP client: install validia[http]" in (
        capsys.readouterr().err
    )


def test_run_names_the_providers_it_can_call(
    example: Cli, fake_wire: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = Cli(
        cwd=example.cwd,
        environ={"META_API_KEY": "x"},
        discovery=HERMETIC,
        interactive=False,
        transport=fake_wire(triage),
    )
    write(example.cwd / "validia.toml", '[providers.meta]\napi_key_env = "META_API_KEY"\n')
    assert cli.main(["run", SUITE, "-m", "meta:llama-4"]) == 1
    assert "cannot run meta:llama-4: validia can call anthropic, openai" in capsys.readouterr().err


# ------------------------------------------------- model access and per-suite


def test_run_dry_run_names_where_the_key_comes_from(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main(["run", SUITE, "-m", "claude-x", "--dry-run"]) == 0
    assert (
        capsys.readouterr()
        .out.splitlines()[-1]
        .endswith(
            "to anthropic:claude-x, 4 at a time, key from ANTHROPIC_API_KEY in the environment."
        )
    )


def test_a_missing_key_says_exactly_what_to_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = Cli(cwd=tmp_path, environ={}, discovery=HERMETIC, interactive=False)
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["run", SUITE, "-m", "openai:gpt-x", "--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines()[-1].endswith("to openai:gpt-x, 4 at a time, but no API key is set.")
    assert (
        "no API key for openai: set FRANCA_OPENAI_API_KEY or OPENAI_API_KEY, in the environment"
        " or in .env, or point [providers.openai] api_key_env at the variable that holds it"
    ) in err
    assert cli.main(["run", SUITE, "-m", "openai:gpt-x"]) == 1


def test_api_key_env_points_at_another_variable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = Cli(cwd=tmp_path, environ={"TEAM_KEY": "sk-team"}, discovery=HERMETIC, interactive=False)
    assert cli.main(["init"]) == 0
    with (tmp_path / "validia.toml").open("a", encoding="utf-8") as settings:
        settings.write('\n[providers.anthropic]\napi_key_env = "TEAM_KEY"\ntimeout_s = 30\n')
    capsys.readouterr()
    assert cli.main(["run", SUITE, "-m", "claude-x", "--dry-run"]) == 0
    assert capsys.readouterr().out.endswith("key from TEAM_KEY in the environment.\n")
    assert cli.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "providers.anthropic.timeout_s = 30" in out


def project_with_dotenv(tmp_path: Path, environ: dict[str, str], dotenv: str) -> Cli:
    """A project whose .env holds the given text; the keys in it are fakes."""
    cli = Cli(cwd=tmp_path, environ=environ, discovery=HERMETIC, interactive=False)
    assert cli.main(["init"]) == 0
    write(tmp_path / ".env", dotenv)
    return cli


def test_a_key_in_the_project_dotenv_is_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = project_with_dotenv(tmp_path, {}, "# keys\nexport OPENAI_API_KEY='sk-file'\n")
    capsys.readouterr()
    assert cli.main(["run", SUITE, "-m", "gpt-x", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("key from OPENAI_API_KEY in .env:2.\n")
    assert "sk-file" not in out


def test_the_shell_outranks_dotenv_unless_it_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dotenv = "ANTHROPIC_API_KEY=sk-file\n"
    shell = project_with_dotenv(tmp_path, {"ANTHROPIC_API_KEY": "sk-shell"}, dotenv)
    capsys.readouterr()
    assert shell.main(["run", SUITE, "-m", "claude-x", "--dry-run"]) == 0
    assert capsys.readouterr().out.endswith("key from ANTHROPIC_API_KEY in the environment.\n")
    blank = Cli(cwd=tmp_path, environ={"ANTHROPIC_API_KEY": ""}, discovery=HERMETIC)
    assert blank.main(["run", SUITE, "-m", "claude-x", "--dry-run"]) == 0
    assert capsys.readouterr().out.endswith("key from ANTHROPIC_API_KEY in .env:1.\n")


def test_config_keys_lists_every_provider_without_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dotenv = "OPENAI_API_KEY=sk-o\nANTHROPIC_API_KEY=sk-a\nGEMINI_API_KEY=sk-g\n"
    cli = project_with_dotenv(tmp_path, {}, dotenv)
    with (tmp_path / "validia.toml").open("a", encoding="utf-8") as settings:
        settings.write('\n[providers.my_gateway]\napi_key = "sk-inline"\n')
    capsys.readouterr()
    assert cli.main(["config", "--keys"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "anthropic   key from ANTHROPIC_API_KEY in .env:2",
        "openai      key from OPENAI_API_KEY in .env:1",
        "google      key from GEMINI_API_KEY in .env:3",
        "xai         - no key: set FRANCA_XAI_API_KEY or XAI_API_KEY,"
        " in the environment or in .env",
        "deepseek    - no key: set FRANCA_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY,"
        " in the environment or in .env",
        "my_gateway  key from providers.my_gateway.api_key in settings",
    ]
    assert "sk-" not in out


def test_a_provider_typo_names_the_file(example: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    write(example.cwd / "validia.toml", "[providers.anthropic]\ntimeout = 30\n")
    assert example.main(["run", SUITE, "-m", "claude-x", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "providers.anthropic.timeout" in err
    assert "validia.toml" in err


def test_a_model_without_a_known_provider_is_refused(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert example.main(["run", SUITE, "-m", "my-model", "--dry-run"]) == 1
    assert "write it as provider:model, as in openai:my-model" in capsys.readouterr().err


def test_a_suite_folder_settings_file_outranks_the_project(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    write(example.cwd / "validia.toml", 'model = "claude-project"\n[run]\nreps = 2\n')
    write(example.cwd / "evals/ticket-triage/validia.toml", 'model = "claude-suite"\n')
    assert example.main(["run", SUITE, "--dry-run"]) == 0
    assert "A run sends 16 trials to anthropic:claude-suite," in capsys.readouterr().out
    assert example.main(["config", "--suite", SUITE]) == 0
    model = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("model"))
    assert model.startswith("model = 'claude-suite'")
    assert model.endswith("evals/ticket-triage/validia.toml")


def test_flags_still_beat_the_suite_settings_file(
    example: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    write(example.cwd / "evals/ticket-triage/validia.toml", 'model = "claude-suite"\n')
    assert example.main(["run", SUITE, "-m", "claude-flag", "--dry-run"]) == 0
    assert "to anthropic:claude-flag," in capsys.readouterr().out


def test_config_shows_a_suite_s_view(example: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    write(example.cwd / "evals/ticket-triage/validia.toml", "[run]\nreps = 5\n")
    assert example.main(["config", "run.reps"]) == 0
    assert capsys.readouterr().out.startswith("run.reps = 1\n")
    for suite in (SUITE, "evals/ticket-triage"):
        assert example.main(["config", "run.reps", "--suite", suite]) == 0
        assert capsys.readouterr().out.startswith("run.reps = 5\n")


def test_a_suite_in_the_project_root_is_not_layered_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = Cli(cwd=tmp_path, environ=KEYS, discovery=HERMETIC, interactive=False)
    write(tmp_path / "prompt.md", "Answer yes or no.\n")
    write(
        tmp_path / "suite.toml",
        'prompt = "prompt.md"\n[grade]\ntype = "label"\nlabels = ["yes"]\n\n'
        '[[cases]]\nid = "a"\ninput = "x"\nexpected = "yes"\n',
    )
    write(tmp_path / "validia.toml", 'model = "claude-root"\n')
    assert cli.main(["config", "model", "--suite", "suite.toml"]) == 0
    out = capsys.readouterr().out
    assert out.count("validia.toml") == 1


# ------------------------------------------------------------------- create


def test_create_builds_a_label_suite_from_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(
        monkeypatch,
        "",
        "intent",
        "1",
        "buy",
        "buy, browse",
        "write",
        "You classify shopper messages.",
        "",
        "Reply with buy or browse.",
        ".",
        "Shopper intent",
        "",
        "Add the red shoes to my cart",
        "buy",
        "1",
        "",
        "",
        "Just looking around",
        "browse",
        "browse",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == ["wrote evals/intent/suite.toml", "wrote evals/intent/prompt.md"]
    assert "next: validia run evals/intent/suite.toml --dry-run" in err
    assert "list at least two" in err
    suite = load_suite(tmp_path / "evals/intent/suite.toml")
    assert suite.grade.labels == ("buy", "browse")
    assert suite.description == "Shopper intent"
    assert [case.id for case in suite.cases] == ["case-1", "case-2"]
    assert suite.groups() == {"buy": 1, "browse": 1}
    prompt = (tmp_path / "evals/intent/prompt.md").read_text(encoding="utf-8")
    assert prompt == "You classify shopper messages.\n\nReply with buy or browse.\n"


def test_a_created_suite_runs_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(
        monkeypatch, "", "quick", "text", "later", "", "", "Hi", "", "hello", "", "", "", "", "n"
    )
    assert asking(tmp_path).main(["create"]) == 0
    capsys.readouterr()
    cli = Cli(cwd=tmp_path, environ=KEYS, discovery=HERMETIC, interactive=False)
    assert cli.main(["run", "evals/quick/suite.toml", "-m", "claude-x", "--dry-run"]) == 0
    assert "evals/quick/suite.toml: 1 case, graded as text" in capsys.readouterr().out
    assert (tmp_path / "evals/quick/prompt.md").read_text(encoding="utf-8").startswith("Write the")


def test_create_points_at_an_existing_prompt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "prompts").mkdir()
    write(tmp_path / "prompts/fields.md", "Reply in JSON.\n")
    answers(
        monkeypatch,
        "",
        "fields",
        "json",
        "category, priority",
        "file",
        "nope.md",
        "prompts/fields.md",
        "",
        "",
        "App freezes",
        "",
        "bug",
        "",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == ["wrote evals/fields/suite.toml"]
    assert "no such file: nope.md" in err
    text = (tmp_path / "evals/fields/suite.toml").read_text(encoding="utf-8")
    assert 'prompt = "../../prompts/fields.md"' in text
    assert 'required = ["category", "priority"]' in text
    suite = load_suite(tmp_path / "evals/fields/suite.toml")
    assert suite.prompt.resolve() == (tmp_path / "prompts/fields.md").resolve()


def test_create_defines_tools_and_a_tool_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(
        monkeypatch,
        "",
        "router",
        "4",
        "1",
        "lookup_order",
        "Look up an order",
        "order_id",
        "",
        "later",
        "",
        "",
        "Where is order 7?",
        "lookup_order",
        "1",
        "7",
        "y",
        "",
        "Thanks!",
        "none",
        "none",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "wrote evals/router/suite.toml",
        "wrote evals/router/prompt.md",
        "wrote evals/router/tools.json",
    ]
    suite = load_suite(tmp_path / "evals/router/suite.toml")
    assert [tool.name for tool in suite.tools] == ["lookup_order"]
    assert suite.summary() == "tool  (lookup_order)"
    first = suite.cases[0].expected
    assert first == ToolUse("lookup_order", {"order_id": "7"})


def test_create_uses_an_existing_tools_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "shared").mkdir()
    tools = [{"name": "search_help", "description": "Search", "parameters": {"type": "object"}}]
    write(tmp_path / "shared/tools.json", json.dumps(tools))
    answers(
        monkeypatch,
        "",
        "help",
        "tool",
        "file",
        "shared/tools.json",
        "later",
        "",
        "",
        "How do I pay?",
        "",
        "search_help",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    text = (tmp_path / "evals/help/suite.toml").read_text(encoding="utf-8")
    assert 'tools = "../../shared/tools.json"' in text
    assert load_suite(tmp_path / "evals/help/suite.toml").tools[0].name == "search_help"


def test_create_flags_skip_their_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    used = answers(monkeypatch, "later", "", "", "Hi", "", "hello", "", "", "", "", "n")
    argv = ["create", "--evals", "quality", "--name", "greet", "--answer", "text"]
    assert asking(tmp_path).main(argv) == 0
    assert used[0] == "later"
    assert (tmp_path / "quality/greet/suite.toml").is_file()


def test_create_refuses_a_name_already_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "evals/taken").mkdir(parents=True)
    used = answers(
        monkeypatch,
        "",
        "",
        "taken",
        "fresh",
        "text",
        "later",
        "",
        "",
        "x",
        "",
        "y",
        "",
        "",
        "",
        "",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    assert used[1:4] == ["", "taken", "fresh"]
    err = capsys.readouterr().err
    assert "a suite needs a name" in err
    assert "evals/taken already exists; choose another name" in err
    assert asking(tmp_path).main(["create", "--evals", "evals", "--name", "taken"]) == 1
    assert "evals/taken already exists" in capsys.readouterr().err


def test_create_needs_a_terminal(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["create"]) == 1
    assert "`create` asks questions" in capsys.readouterr().err


def test_create_cancelled_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pending = iter(["", "gone", "label", "a, b"])

    def then_eof() -> str:
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None  # what Ctrl-D does

    monkeypatch.setattr("builtins.input", then_eof)
    assert asking(tmp_path).main(["create"]) == 1
    assert "validia: cancelled; nothing was written" in capsys.readouterr().err
    assert not (tmp_path / "evals").exists()


def test_create_builds_the_prompt_from_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers(
        monkeypatch,
        "",
        "triage",
        "label",
        "urgent, normal",
        "",  # build is the default
        "the support assistant for Acme Shop",
        "sort tickets by how soon they need a person",
        "",
        "NEVER guess",
        "",
        "judge by what is happening, because calm customers report outages too",
        "",
        "",
        "",  # first closing instruction, then use the prompt
        "",
        "",
        "Site is down",
        "",
        "urgent",
        "n",
    )
    assert asking(tmp_path).main(["create"]) == 0
    err = capsys.readouterr().err
    assert "Building the prompt from the built-in prompt template." in err
    assert "hint caps: capitals add pressure" in err
    prompt = (tmp_path / "evals/triage/prompt.md").read_text(encoding="utf-8")
    assert prompt == (
        "You are the support assistant for Acme Shop.\n\n"
        "Your job is to sort tickets by how soon they need a person.\n\n"
        "Keep to these rules:\n"
        "- judge by what is happening, because calm customers report outages too\n\n"
        "Reply with one word, urgent or normal, and nothing else.\n"
    )


def test_create_uses_the_project_prompt_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        tmp_path / "prompt.toml",
        '[[sections]]\nid = "goal"\nask = "Goal?"\ntemplate = "Goal: {answer}"\n\n'
        '[output]\nlabel = ["Answer {labels}."]\njson = ["x"]\ntext = ["x"]\ntool = ["x"]\n',
    )
    answers(
        monkeypatch, "", "mine", "label", "a, b", "build", "win", "", "", "", "", "q", "", "a", "n"
    )
    assert asking(tmp_path).main(["create"]) == 0
    assert "Building the prompt from prompt.toml." in capsys.readouterr().err
    prompt = (tmp_path / "evals/mine/prompt.md").read_text(encoding="utf-8")
    assert prompt == "Goal: win\n\nAnswer a or b.\n"


def test_a_broken_project_template_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.toml", "[[sections]]\nid = 'x'\n")
    answers(monkeypatch, "", "mine", "label", "a, b", "build")
    assert asking(tmp_path).main(["create"]) == 1
    err = capsys.readouterr().err
    assert "prompt.toml has 2 problems" in err
    assert "sections[0].ask: missing" in err
    assert not (tmp_path / "evals/mine").exists()


def test_template_prints_the_built_in_template(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["template"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# How `validia create` builds a prompt")
    write(cli.cwd / "prompt.toml", out)
    assert load_template(cli.cwd / "prompt.toml").sections == default_template().sections


# ---------------------------------------------------------------------- add


def project(cli: Cli, answer: str, capsys: pytest.CaptureFixture[str]) -> str:
    """Scaffold one example suite and return its path."""
    assert cli.main(["init", "--answer", answer]) == 0
    capsys.readouterr()
    name = {"label": "ticket-triage", "json": "ticket-fields", "text": "help-answers"}.get(
        answer, "support-tools"
    )
    return f"evals/{name}/suite.toml"


def test_add_asks_for_a_label_case_and_lets_you_try_it(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "label", capsys)
    answers(
        monkeypatch,
        "",
        "I want a refund",
        "normal, billing",
        "later",
        "normal",
        "Normal.",
        "urgent",
        "",
    )
    assert asking(tmp_path).main(["add", suite]) == 0
    out, err = capsys.readouterr()
    assert "Adding a case to evals/ticket-triage/suite.toml: label  (urgent, normal)" in err
    assert "Case id [case-9]: " in err
    assert "Expected label\n  1  urgent\n  2  normal\n" in err
    assert "choose 1-2, or a name: urgent, normal" in err
    lines = out.splitlines()
    assert lines[0] == f"added case-9 to {suite}"
    assert lines[1:6] == [
        "[[cases]]",
        'id = "case-9"',
        'tags = ["normal", "billing"]',
        'input = "I want a refund"',
        'expected = "normal"',
    ]
    assert lines[6:] == ["pass", "fail: expected 'normal', got 'urgent'"]
    assert len(load_suite(tmp_path / suite).cases) == 9


def test_add_refuses_a_taken_or_malformed_id(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "label", capsys)
    answers(monkeypatch, "outage-login", "bad id", "fresh", "", "x", "", "urgent", "")
    assert asking(tmp_path).main(["add", suite]) == 0
    err = capsys.readouterr().err
    assert "'outage-login' is already a case here" in err
    assert "use letters, digits" in err
    assert "a case needs an input" in err
    assert load_suite(tmp_path / suite).cases[-1].id == "fresh"


def test_add_asks_one_question_per_json_field(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "json", capsys)
    answers(
        monkeypatch, "", "App freezes", "", "bug", "null", "", "mobile", '{"category": "bug"}', ""
    )
    assert asking(tmp_path).main(["add", suite]) == 0
    out, err = capsys.readouterr()
    assert "TOML has no null" in err
    assert 'expected = { category = "bug", product = "mobile" }' in out
    assert "fail: priority: missing; product: missing" in out


def test_add_takes_a_json_object_when_no_keys_are_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "prompt.md", "Reply in JSON.\n")
    write(
        tmp_path / "suite.toml",
        'prompt = "prompt.md"\n[grade]\ntype = "json"\n\n'
        '[[cases]]\nid = "a"\ninput = "x"\nexpected = { n = 1 }\n',
    )
    answers(monkeypatch, "", "y", "", "[1]", '{"n": 2}', "")
    assert asking(tmp_path).main(["add", "suite.toml"]) == 0
    out, err = capsys.readouterr()
    assert "expected a JSON object" in err
    assert "expected = { n = 2 }" in out


def test_add_builds_text_checks_and_tests_the_regex(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "text", capsys)
    replies = [
        "",
        "Can I print a note?",
        "covered",
        "",
        "",
        "",
        "",  # no checks at all: asked again
        "Print",
        "",
        "fax",
        "",
        "yes|",
        r"(?i)\bprint\b",
        "",
        "Use File > Print.",
        "We can fax it.",
        "",
    ]
    answers(monkeypatch, *replies)
    assert asking(tmp_path).main(["add", suite]) == 0
    out, err = capsys.readouterr()
    assert "a text case needs at least one check" in err
    assert "an empty reply matches it too" in err
    assert (
        'expected = { contains = ["Print"], not_contains = ["fax"], matches = \'(?i)\\bprint\\b\' }'
        in out
    )
    assert out.splitlines()[-2:] == [
        "pass",
        "fail: missing 'Print'; says 'fax'; does not match '(?i)\\\\bprint\\\\b'",
    ]


def test_add_asks_for_a_tool_and_its_arguments(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "tool", capsys)
    replies = [
        "",
        "Where is order 5?",
        "lookup_order",
        "lookup",
        "lookup_order",
        "5",
        "lookup_order",
        '{"order_id": "5"}',
        "none",
        "search",
        "",
    ]
    answers(monkeypatch, *replies)
    assert asking(tmp_path).main(["add", suite]) == 0
    out, err = capsys.readouterr()
    assert "choose 1-4, or a name: lookup_order, search_help, create_ticket, none" in err
    assert 'expected = { tool = "lookup_order", args = { order_id = "5" } }' in out
    assert out.splitlines()[-2:] == [
        "pass",
        "fail: expected a call to lookup_order, got no tool, said 'answered without a tool'",
    ]


def test_add_can_expect_no_tool(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "tool", capsys)
    answers(monkeypatch, "", "Bye!", "none", "none", "")
    assert asking(tmp_path).main(["add", suite]) == 0
    assert 'expected = { tool = "none" }' in capsys.readouterr().out


def test_add_needs_a_terminal(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    suite = project(cli, "label", capsys)
    assert cli.main(["add", suite]) == 1
    assert "`add` asks questions" in capsys.readouterr().err


def test_add_cancelled_writes_nothing(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "label", capsys)
    before = (tmp_path / suite).read_text(encoding="utf-8")
    pending = iter(["", "hello"])

    def then_gone() -> str:
        return next(pending)  # StopIteration on the third question, like Ctrl-D

    monkeypatch.setattr("builtins.input", then_gone)
    with pytest.raises(StopIteration):
        asking(tmp_path).main(["add", suite])
    assert (tmp_path / suite).read_text(encoding="utf-8") == before


def test_several_problems_are_listed_one_per_line(
    cli: Cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = project(cli, "label", capsys)

    def refuse(*_: object) -> None:
        raise SpecError(["id: taken", "input: empty"])

    monkeypatch.setattr("validia.cli.suite_commands.find_case", refuse)
    assert cli.main(["check", suite, "outage-login", "--reply", "x"]) == 1
    assert capsys.readouterr().err == "validia: 2 problems:\n  id: taken\n  input: empty\n"


# -------------------------------------------------------------------- check


def test_check_passes_and_fails_with_exit_codes(
    cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = project(cli, "label", capsys)
    assert cli.main(["check", suite, "outage-login", "--reply", "Urgent."]) == 0
    assert capsys.readouterr().out == "pass\n"
    assert cli.main(["check", suite, "outage-login", "--reply", "normal"]) == 1
    assert capsys.readouterr().out == "fail: expected 'urgent', got 'normal'\n"


def test_check_tests_a_regex_without_a_model(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    suite = project(cli, "text", capsys)
    assert cli.main(["check", suite, "web-offline", "--reply", "No - use the desktop app."]) == 0
    assert cli.main(["check", suite, "web-offline", "--reply", "Yes, it works offline."]) == 1
    assert "missing 'desktop'" in capsys.readouterr().out


def test_check_takes_a_tool_call(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    suite = project(cli, "tool", capsys)
    argv = [
        "check",
        suite,
        "order-status",
        "--tool",
        "lookup_order",
        "--args",
        '{"order_id": "48213"}',
    ]
    assert cli.main(argv) == 0
    assert cli.main(["check", suite, "order-status", "--tool", "search_help"]) == 1


def test_check_args_must_be_a_json_object(cli: Cli) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["check", "s.toml", "c", "--tool", "t", "--args", "[1]"])
    assert caught.value.code == 2


def test_check_names_the_closest_case(cli: Cli, capsys: pytest.CaptureFixture[str]) -> None:
    suite = project(cli, "label", capsys)
    assert cli.main(["check", suite, "outage-logn", "--reply", "x"]) == 1
    assert "no case 'outage-logn'" in capsys.readouterr().err
    assert cli.main(["check", suite, "zzz", "--reply", "x"]) == 1


def test_check_reads_the_reply_from_stdin(
    cli: Cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    suite = project(cli, "label", capsys)
    monkeypatch.setattr(sys, "stdin", io.StringIO("urgent\n"))
    assert cli.main(["check", suite, "outage-login"]) == 0


def test_check_asks_for_the_reply_in_a_terminal(
    cli: Cli,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    suite = project(cli, "label", capsys)
    answers(monkeypatch, "urgent")
    assert asking(tmp_path).main(["check", suite, "outage-login"]) == 0
    answers(monkeypatch, "")
    assert asking(tmp_path).main(["check", suite, "outage-login"]) == 1
    assert "no reply to check" in capsys.readouterr().err


# -------------------------------------------------------------- entry points


def test_console_script_entry_point(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert validia.__version__ in capsys.readouterr().out


def test_python_dash_m(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["validia", "--version"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("validia", run_name="__main__")
    assert caught.value.code == 0
    assert validia.__version__ in capsys.readouterr().out
