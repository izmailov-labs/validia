"""The precedence matrix. This test *is* the specification.

The same key is set in every source at once and the winner is asserted, then
each higher source is removed in turn so every rung of the ladder is proved.
"""

from pathlib import Path

import pytest

from whence import Config, Discovery

LAYERS = ["overrides", "env", "dotenv", "secrets-dir", "file", "pyproject", "defaults"]


@pytest.fixture
def world(tmp_path: Path) -> Path:
    """Set `a.k` in every source at once, each with a distinguishable value."""
    (tmp_path / "myapp.toml").write_text('[a]\nk = "file"\n', encoding="utf-8")
    (tmp_path / ".env").write_text("MYAPP_A__K=dotenv\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[tool.myapp.a]\nk = "pyproject"\n', encoding="utf-8")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "a__k").write_text("secrets-dir", encoding="utf-8")
    return tmp_path


def _load(world: Path, drop: set[str]) -> Config:
    d = Discovery(
        "myapp",
        user_config=False,
        secrets_dir=None if "secrets-dir" in drop else world / "secrets",
        dotenv=() if "dotenv" in drop else (Path(".env"),),
        pyproject_table="" if "pyproject" in drop else None,
        path=() if "file" in drop else (Path(),),
    )
    return Config.load(
        discovery=d,
        cwd=world,
        environ={} if "env" in drop else {"MYAPP_A__K": "env"},
        overrides=None if "overrides" in drop else {"a": {"k": "overrides"}},
        defaults={"a": {"k": "defaults"}},
    )


@pytest.mark.parametrize("cut", range(len(LAYERS)))
def test_each_layer_wins_once_the_ones_above_it_are_gone(world: Path, cut: int) -> None:
    config = _load(world, drop=set(LAYERS[:cut]))
    assert config.get("a.k") == LAYERS[cut]


def test_the_full_shadow_chain_is_recorded(world: Path) -> None:
    config = _load(world, drop=set())
    explanation = config.explain("a.k")
    assert "'overrides'" in explanation
    for loser in LAYERS[1:]:
        assert loser in explanation or loser.replace("-", "") in explanation


def test_secrets_dir_outranks_config_files(world: Path) -> None:
    """Inverted relative to pydantic-settings: a mounted secret is deployment truth."""
    config = _load(world, drop={"overrides", "env", "dotenv"})
    assert config.get("a.k") == "secrets-dir"


def test_defaults_are_visible_to_get_and_explain(world: Path) -> None:
    """Spring's split, where defaults live only in the object, is a real bug."""
    config = _load(world, drop=set(LAYERS[:-1]))
    assert config.get("a.k") == "defaults"
    assert "<defaults>" in config.explain("a.k")


def test_the_shadowed_section_appears_only_when_something_was_shadowed() -> None:
    """A bare "shadowed:" with no list under it reads as a truncated report."""
    from whence import Config

    single = Config.from_mapping({"a": {"k": "only"}})
    assert "shadowed" not in single.explain("a.k")
    assert "shadowed" in single.with_fallback(Config.from_mapping({"a": {"k": "under"}})).explain(
        "a.k"
    )


def test_an_absent_key_still_lists_what_was_consulted(world: Path) -> None:
    config = _load(world, drop=set())
    report = config.explain("nothing.here")
    assert "is not set" in report
    assert "consulted" in report
