"""The provenance spine: equality transparency is what makes it retrofittable."""

from whence import Origin, Tracked, origin_of, unwrap


def test_tracked_compares_as_its_value() -> None:
    tracked = Tracked("localhost", Origin("file", "a.yaml", 2, 9))
    assert tracked == "localhost"
    assert tracked == "localhost"
    assert tracked != "other"
    assert tracked == Tracked("localhost", Origin("env", "X"))


def test_tracked_hashes_as_its_value() -> None:
    origin = Origin("file", "a.yaml")
    assert hash(Tracked("x", origin)) == hash("x")
    assert {Tracked("x", origin): 1}["x"] == 1  # type: ignore[index]


def test_tracked_formats_as_its_value() -> None:
    tracked = Tracked(5432, Origin("file", "a.yaml"))
    assert str(tracked) == "5432"
    assert f"{tracked}" == "5432"
    assert bool(Tracked("", Origin("x", "y"))) is False
    assert "a.yaml" in repr(tracked)


def test_origin_renders_position_and_profile() -> None:
    assert str(Origin("file", "a.yaml")) == "a.yaml"
    assert str(Origin("file", "a.yaml", 4)) == "a.yaml:4"
    assert str(Origin("file", "a.yaml", 4, 9)) == "a.yaml:4:9"
    assert str(Origin("file", "a.yaml", 4, 9, "prod")) == "a.yaml:4:9 [profile=prod]"


def test_origin_records_derivation() -> None:
    base = Origin("env", "DB_PASSWORD_FILE")
    derived = base.derived(locator="/run/secrets/pw")
    assert derived.parent is base
    assert "<-" in str(derived)


def test_unwrap_is_recursive() -> None:
    origin = Origin("file", "a.yaml")
    nested = {"a": [Tracked(1, origin), (Tracked(2, origin),)], "b": Tracked("x", origin)}
    assert unwrap(nested) == {"a": [1, (2,)], "b": "x"}
    assert unwrap("plain") == "plain"


def test_origin_of_returns_none_for_untracked() -> None:
    assert origin_of("plain") is None
    assert origin_of(Tracked(1, Origin("a", "b"))) is not None
