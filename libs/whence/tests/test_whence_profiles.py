"""Profiles: ordered lists, groups, and the invariant that they never reorder."""

from pathlib import Path

import pytest

from whence import Config, ConfigError, Discovery, active_profiles, expand_groups


def test_profile_lists_are_ordered_and_later_wins() -> None:
    assert active_profiles(["dev", "prod"]) == ("dev", "prod")


def test_groups_expand_before_the_profile_that_named_them() -> None:
    got = expand_groups(["staging"], {"staging": ["cloud", "readonly-db"]})
    assert got == ("cloud", "readonly-db", "staging")


def test_groups_nest() -> None:
    groups = {"a": ["b"], "b": ["c"]}
    assert expand_groups(["a"], groups) == ("c", "b", "a")


def test_a_group_cycle_is_reported() -> None:
    with pytest.raises(ConfigError, match="cycle"):
        expand_groups(["a"], {"a": ["b"], "b": ["a"]})


def test_profiles_come_from_the_environment_when_not_given() -> None:
    got = active_profiles(None, environ={"MYAPP_PROFILES": "dev, prod"}, var="MYAPP_PROFILES")
    assert got == ("dev", "prod")


def test_explicit_profiles_beat_the_environment() -> None:
    got = active_profiles(["local"], environ={"MYAPP_PROFILES": "prod"}, var="MYAPP_PROFILES")
    assert got == ("local",)


def test_duplicates_collapse_keeping_first_position() -> None:
    assert active_profiles(["a", "b", "a"]) == ("a", "b")


def test_profiles_never_let_a_lower_source_beat_a_higher_one(tmp_path: Path) -> None:
    """The invariant. Quarkus states it; Spring had to be redesigned around it.

    A profiled *file* is still a file: it cannot outrank the environment.
    """
    (tmp_path / "myapp.prod.toml").write_text('[a]\nk = "from-profile-file"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        profiles=["prod"],
        environ={"MYAPP_A__K": "from-env"},
    )
    assert config.get("a.k") == "from-env"


def test_a_profile_file_still_beats_the_base_file(tmp_path: Path) -> None:
    (tmp_path / "myapp.toml").write_text('[a]\nk = "base"\n', encoding="utf-8")
    (tmp_path / "myapp.prod.toml").write_text('[a]\nk = "prod"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        profiles=["prod"],
        environ={},
    )
    assert config.get("a.k") == "prod"
    assert config.origin("a.k").profile == "prod"  # type: ignore[union-attr]
