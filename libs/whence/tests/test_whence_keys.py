"""Canonical keys, and the bijection with environment variable names."""

import pytest

from whence import ConfigError, canonical, env_name, key_from_env
from whence.keys import default_prefix, flatten, join, suggest, validate_prefix


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("db.host", ("db", "host")),
        ("DB.Host", ("db", "host")),
        ("db.max-retries", ("db", "max_retries")),
        ("db.max_retries", ("db", "max_retries")),
        ("  db . host ", ("db", "host")),
        (("db", "HOST"), ("db", "host")),
    ],
)
def test_canonical_folds_case_and_dashes(raw: object, expected: tuple[str, ...]) -> None:
    assert canonical(raw) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "db..host", "  ", "db."])
def test_canonical_rejects_empty_segments(bad: str) -> None:
    with pytest.raises(ConfigError):
        canonical(bad)


def test_env_mapping_is_bijective() -> None:
    """The whole reason whence needs no fuzzy relaxed binding."""
    for path in [("db", "host"), ("db", "max_retries"), ("a",), ("a", "b", "c")]:
        name = env_name(path, "MYAPP_")
        assert key_from_env(name, "MYAPP_") == path


def test_single_underscore_stays_inside_a_segment() -> None:
    """Spring cannot distinguish these two; whence can."""
    assert key_from_env("MYAPP_DB__MAX_RETRIES", "MYAPP_") == ("db", "max_retries")
    assert key_from_env("MYAPP_DB__MAX__RETRIES", "MYAPP_") == ("db", "max", "retries")


def test_key_from_env_ignores_foreign_names() -> None:
    assert key_from_env("PATH", "MYAPP_") is None
    assert key_from_env("MYAPP_", "MYAPP_") is None
    assert key_from_env("MYAPP___X", "MYAPP_") is None


def test_prefix_containing_the_delimiter_is_rejected() -> None:
    """pydantic-settings has a live bug here because it does not check."""
    with pytest.raises(ConfigError, match="nesting delimiter"):
        validate_prefix("MY__APP_")
    assert validate_prefix("MYAPP_") == "MYAPP_"


def test_default_prefix_derives_from_the_app_name() -> None:
    assert default_prefix("myapp") == "MYAPP_"


def test_join_round_trips() -> None:
    assert join(canonical("db.host")) == "db.host"


def test_flatten_treats_lists_as_leaves() -> None:
    flat = flatten({"db": {"hosts": ["a", "b"], "port": 1}, "empty": {}})
    assert flat == {("db", "hosts"): ["a", "b"], ("db", "port"): 1, ("empty",): {}}


def test_suggest_finds_near_misses() -> None:
    assert suggest(("db", "max_retires"), [("db", "max_retries")]) == ["db.max_retries"]
    assert suggest(("totally", "different"), [("db", "host")]) == []
