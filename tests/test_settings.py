"""Settings resolve through whence: defaults, the starter file, precedence, validation."""

import re
import sys
from pathlib import Path

import pytest
from whence import BindError, Discovery

from validia.cli.settings import KEYS, RuleVersions, RunSettings, Settings, load
from validia.rules import CATEGORIES
from validia.suites import scaffold

# No per-user config directory and no /run/secrets: only what a test writes counts.
HERMETIC = Discovery(app="validia", user_config=False, secrets_dir=None)


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_nothing_configured_binds_the_defaults(tmp_path: Path) -> None:
    config, settings = load(discovery=HERMETIC, environ={}, cwd=tmp_path)
    assert settings == Settings()
    assert config.get("run.reps") == 1
    assert "model" not in config


def test_keys_cover_the_schema_in_declaration_order() -> None:
    assert KEYS == (
        "model",
        "run.reps",
        "run.concurrency",
        "run.retries",
        "run.output",
        "run.fail_under",
        "lint.fail_on",
        "lint.rules.wording",
        "lint.rules.context",
        "lint.rules.reasoning",
        "lint.rules.output",
        "lint.rules.tools",
        "lint.rules.security",
        "lint.rules.maintenance",
    )


def test_rule_pins_cover_every_core_category() -> None:
    assert tuple(RuleVersions().pinned()) == CATEGORIES
    assert set(RuleVersions().pinned().values()) == {"latest"}


def test_the_starter_file_states_the_defaults(tmp_path: Path) -> None:
    settings_file = write(tmp_path / "validia.toml", scaffold.read(scaffold.SETTINGS))
    config, settings = load(discovery=HERMETIC, environ={}, cwd=tmp_path)
    assert settings == Settings()
    origin = config.origin("run.concurrency")
    assert origin is not None
    assert origin.locator == str(settings_file)


def test_precedence_flag_then_set_then_env_then_file(tmp_path: Path) -> None:
    write(tmp_path / "validia.toml", "[run]\nreps = 2\n")
    env = {"VALIDIA_RUN__REPS": "3"}
    argv = ["--set", "run.reps=4"]

    def reps(**kwargs: object) -> int:
        _, settings = load(discovery=HERMETIC, cwd=tmp_path, **kwargs)  # type: ignore[arg-type]
        return settings.run.reps

    assert reps(environ={}) == 2
    assert reps(environ=env) == 3
    assert reps(environ=env, argv=argv) == 4
    assert reps(environ=env, argv=argv, overrides={"run.reps": 5}) == 5


def test_a_profile_overlays_the_base_file(tmp_path: Path) -> None:
    write(tmp_path / "validia.toml", 'model = "base"\n[run]\nreps = 2\n')
    write(tmp_path / "validia.ci.toml", "[run]\nreps = 9\n")
    _, settings = load(discovery=HERMETIC, profiles=["ci"], environ={}, cwd=tmp_path)
    assert settings.model == "base"
    assert settings.run == RunSettings(reps=9)


def test_out_of_range_values_are_reported_together_with_their_origin(tmp_path: Path) -> None:
    write(tmp_path / "validia.toml", "[run]\nreps = 0\nconcurrency = 0\n")
    with pytest.raises(BindError) as caught:
        load(discovery=HERMETIC, environ={}, cwd=tmp_path)
    message = str(caught.value)
    assert message.startswith("2 errors")
    assert "run.reps" in message
    assert "run.concurrency" in message
    assert "must be at least 1" in message
    assert "validia.toml" in message


def test_an_unknown_key_suggests_the_one_meant(tmp_path: Path) -> None:
    write(tmp_path / "validia.toml", "[run]\nrepz = 3\n")
    with pytest.raises(BindError, match=re.escape("did you mean 'run.reps'")):
        load(discovery=HERMETIC, environ={}, cwd=tmp_path)


def test_a_wrong_choice_is_rejected(tmp_path: Path) -> None:
    write(tmp_path / "validia.toml", '[lint]\nfail_on = "sometimes"\n')
    with pytest.raises(BindError, match=re.escape("lint.fail_on")):
        load(discovery=HERMETIC, environ={}, cwd=tmp_path)


def test_a_library_call_never_reads_sys_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["validia", "--set", "run.reps=9"])
    _, settings = load(discovery=HERMETIC, environ={}, cwd=tmp_path)
    assert settings.run.reps == 1
