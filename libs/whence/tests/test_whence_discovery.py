"""The five-step discovery chain, asserted as a matrix.

Every step, every failure mode, and the source *order* that results -- not
merely that some file was found.
"""

from pathlib import Path

import pytest

from whence import AmbiguousConfigError, ConfigError, Discovery, MissingConfigError


def _write(root: Path, name: str, body: str = "a = 1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


def test_step1_explicit_file_wins(tmp_path: Path) -> None:
    explicit = _write(tmp_path, "explicit.toml")
    _write(tmp_path / "config", "myapp.toml")
    plan = Discovery("myapp", file=explicit, path=(Path("config"),), user_config=False).plan(
        cwd=tmp_path
    )
    assert plan.files[0][0] == explicit


def test_step1_missing_explicit_file_is_an_error(tmp_path: Path) -> None:
    """An explicit path that does not exist is always a mistake."""
    with pytest.raises(MissingConfigError, match="named explicitly"):
        Discovery("myapp", file=tmp_path / "nope.toml").plan(cwd=tmp_path)


def test_step2_env_var_beats_the_search_path(tmp_path: Path) -> None:
    pointed = _write(tmp_path, "pointed.toml")
    _write(tmp_path / "config", "myapp.toml")
    plan = Discovery("myapp", path=(Path("config"),), user_config=False).plan(
        environ={"MYAPP_CONFIG": str(pointed)}, cwd=tmp_path
    )
    assert plan.files[0][0] == pointed
    assert plan.steps[1].found


def test_step2_missing_target_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MissingConfigError, match=r"\$MYAPP_CONFIG"):
        Discovery("myapp").plan(environ={"MYAPP_CONFIG": "/nope"}, cwd=tmp_path)


def test_step3_profile_files_outrank_the_base_and_each_other(tmp_path: Path) -> None:
    _write(tmp_path, "myapp.toml")
    _write(tmp_path, "myapp.dev.toml")
    _write(tmp_path, "myapp.prod.toml")
    plan = Discovery("myapp", user_config=False).plan(profiles=["dev", "prod"], cwd=tmp_path)
    assert [p.name for p, _ in plan.files] == ["myapp.prod.toml", "myapp.dev.toml", "myapp.toml"]
    assert [prof for _, prof in plan.files] == ["prod", "dev", None]


def test_layer_mode_keeps_every_root_and_first_mode_stops(tmp_path: Path) -> None:
    _write(tmp_path / "a", "myapp.toml")
    _write(tmp_path / "b", "myapp.toml")
    roots = (Path("a"), Path("b"))
    layered = Discovery("myapp", path=roots, user_config=False).plan(cwd=tmp_path)
    assert len(layered.files) == 2
    first = Discovery("myapp", path=roots, user_config=False, mode="first").plan(cwd=tmp_path)
    assert len(first.files) == 1
    assert first.files[0][0].parent.name == "a"


def test_two_formats_in_one_root_is_ambiguous(tmp_path: Path) -> None:
    """The case Spring left unspecified for years."""
    _write(tmp_path, "myapp.toml")
    _write(tmp_path, "myapp.json", "{}")
    with pytest.raises(AmbiguousConfigError, match="more than one"):
        Discovery("myapp", user_config=False).plan(cwd=tmp_path)


def test_narrowing_formats_resolves_ambiguity_and_bans_a_format(tmp_path: Path) -> None:
    _write(tmp_path, "myapp.toml")
    _write(tmp_path, "myapp.json", "{}")
    plan = Discovery("myapp", formats=("toml",), user_config=False).plan(cwd=tmp_path)
    assert [p.name for p, _ in plan.files] == ["myapp.toml"]


def test_two_roots_holding_the_same_name_is_layering_not_ambiguity(tmp_path: Path) -> None:
    _write(tmp_path / "a", "myapp.toml")
    _write(tmp_path / "b", "myapp.json", "{}")
    plan = Discovery("myapp", path=(Path("a"), Path("b")), user_config=False).plan(cwd=tmp_path)
    assert len(plan.files) == 2


def test_search_parents_walks_up_and_stops_at_the_boundary(tmp_path: Path) -> None:
    """A uv workspace runs tests from libs/<member>/ while config sits at the root."""
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "config", "myapp.toml")
    deep = tmp_path / "libs" / "member"
    deep.mkdir(parents=True)
    found = Discovery("myapp", path=(Path("config"),), search_parents=True, user_config=False).plan(
        cwd=deep
    )
    assert found.files
    assert found.files[0][0].parent.parent == tmp_path
    missed = Discovery("myapp", path=(Path("config"),), user_config=False).plan(cwd=deep)
    assert not missed.files


def test_search_parents_does_not_escape_the_boundary(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _write(tmp_path, "myapp.toml")  # above the boundary; must not be found
    deep = root / "libs"
    deep.mkdir()
    plan = Discovery("myapp", search_parents=True, user_config=False).plan(cwd=deep)
    assert not plan.files


def test_step4_user_config_dir_is_searched(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write(home / ".config" / "myapp", "myapp.toml")
    plan = Discovery("myapp", path=(), user_config=True).plan(
        environ={"HOME": str(home)}, cwd=tmp_path, platform="linux"
    )
    assert plan.files
    assert plan.files[0][0].parent.name == "myapp"


def test_on_missing_error_lists_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(MissingConfigError, match="Searched"):
        Discovery("myapp", user_config=False, on_missing="error").plan(cwd=tmp_path)


def test_every_step_is_reported_even_when_it_found_nothing(tmp_path: Path) -> None:
    plan = Discovery("myapp", user_config=False).plan(cwd=tmp_path)
    report = plan.render()
    assert "$MYAPP_CONFIG" in report
    assert "not set" in report
    assert {step.index for step in plan.steps} == {1, 2, 3, 4, 5}


def test_derived_names_all_come_from_the_app_name() -> None:
    d = Discovery("myapp")
    assert (d.env_prefix, d.config_var, d.profiles_env, d.table) == (
        "MYAPP_",
        "MYAPP_CONFIG",
        "MYAPP_PROFILES",
        "tool.myapp",
    )


def test_every_derived_name_can_be_overridden() -> None:
    d = Discovery(
        "franca",
        prefix="VALIDIA_",
        env_var="V_CFG",
        profiles_var="V_PROFILES",
        pyproject_table="tool.v",
    )
    assert (d.env_prefix, d.config_var, d.profiles_env, d.table) == (
        "VALIDIA_",
        "V_CFG",
        "V_PROFILES",
        "tool.v",
    )


def test_a_prefix_holding_the_delimiter_is_rejected_at_construction() -> None:
    with pytest.raises(ConfigError, match="nesting delimiter"):
        Discovery("myapp", prefix="MY__APP_")


def test_with_returns_a_modified_copy() -> None:
    base = Discovery("myapp")
    assert base.with_(mode="first").mode == "first"
    assert base.mode == "layer"


def test_pyproject_table_can_be_disabled() -> None:
    assert Discovery("myapp", pyproject_table="").table == ""
