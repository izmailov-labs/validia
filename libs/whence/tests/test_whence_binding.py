"""Binding onto dataclasses and pydantic models, and the diagnostics."""

import datetime as dt
import enum
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import pytest

from whence import BindError, Config, Secret, unlock_secrets
from whence.binding import DataclassBinder, choose_binder, pydantic_available
from whence.binding.coerce import CoercionError, coerce


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432
    tags: list[str] = field(default_factory=list)
    mode: Mode = Mode.FAST
    level: Literal["a", "b"] = "a"
    root: Path = Path()
    timeout: dt.timedelta = dt.timedelta(seconds=30)
    password: Secret | None = None


@dataclass(frozen=True, slots=True)
class App:
    db: Db
    debug: bool = False


@pytest.mark.parametrize(
    ("raw", "annotation", "expected"),
    [
        ("yes", bool, True),
        ("off", bool, False),
        (1, bool, True),
        ("5432", int, 5432),
        ("1.5", float, 1.5),
        (3, str, "3"),
        ("a,b, c", list[str], ["a", "b", "c"]),
        ('["a","b"]', list[str], ["a", "b"]),
        ("1,2", list[int], [1, 2]),
        ('{"a": 1}', dict[str, int], {"a": 1}),
        ("fast", Mode, Mode.FAST),
        ("/opt/x", Path, Path("/opt/x")),
        ("30", dt.timedelta, dt.timedelta(seconds=30)),
        ("1h30m", dt.timedelta, dt.timedelta(minutes=90)),
        ("500ms", dt.timedelta, dt.timedelta(milliseconds=500)),
        ("2024-01-02", dt.date, dt.date(2024, 1, 2)),
        ("", str | None, None),
        ("b", Literal["a", "b"], "b"),
    ],
)
def test_coercion_table(raw: object, annotation: object, expected: object) -> None:
    assert coerce(raw, annotation) == expected


def test_uuid_and_datetime_coercion() -> None:
    assert coerce("12345678-1234-5678-1234-567812345678", uuid.UUID) == uuid.UUID(
        "12345678-1234-5678-1234-567812345678"
    )
    assert coerce("2024-01-02T03:04:05", dt.datetime).hour == 3


def test_a_list_is_json_when_it_opens_with_a_bracket_else_comma_split() -> None:
    """Never a global guess: the two conventions are irreconcilable."""
    assert coerce("[1, 2]", list[int]) == [1, 2]
    assert coerce("1, 2", list[int]) == [1, 2]


@pytest.mark.parametrize(
    ("raw", "annotation"),
    [
        ("maybe", bool),
        ("nope", int),
        ("nope", Mode),
        ("c", Literal["a", "b"]),
        ("1x2y", dt.timedelta),
        ("[1", dict[str, int]),
    ],
)
def test_coercion_failures_are_typed(raw: str, annotation: object) -> None:
    with pytest.raises((CoercionError, ValueError)):
        coerce(raw, annotation)


def test_binds_a_flat_dataclass() -> None:
    config = Config.from_mapping({"db": {"host": "h", "port": "9999", "tags": "a,b"}})
    db = config.bind(Db, prefix="db")
    assert db.host == "h"
    assert db.port == 9999
    assert db.tags == ["a", "b"]


def test_binds_nested_dataclasses() -> None:
    config = Config.from_mapping({"db": {"host": "h"}, "debug": "true"})
    app = config.bind(App)
    assert app.db.host == "h"
    assert app.debug is True


def test_defaults_apply_when_a_key_is_absent() -> None:
    assert Config.from_mapping({}).bind(Db, prefix="db").port == 5432


def test_a_required_field_with_no_value_is_reported() -> None:
    @dataclass(frozen=True)
    class Required:
        needed: str

    with pytest.raises(BindError, match="required"):
        Config.from_mapping({}).bind(Required)


def test_a_failed_bind_does_not_report_the_same_mistake_twice() -> None:
    """The binder used to construct the target even after collecting problems.

    The `TypeError` that followed named the very field already reported, and was
    appended as a second `<root>` problem -- so one missing field read as two
    errors, the second an implementation detail.
    """

    @dataclass(frozen=True)
    class Required:
        needed: str

    with pytest.raises(BindError) as caught:
        Config.from_mapping({}).bind(Required)
    message = str(caught.value)
    assert "1 error" in message
    assert "<root>" not in message


def test_every_error_is_reported_in_one_run() -> None:
    """Four mistakes should take one run to fix, not four."""
    config = Config.from_mapping({"db": {"port": "nope", "level": "z", "mode": "sideways"}})
    with pytest.raises(BindError) as caught:
        config.bind(Db, prefix="db")
    message = str(caught.value)
    assert "3 errors" in message
    for key in ("db.port", "db.level", "db.mode"):
        assert key in message


def test_no_error_message_contains_the_rejected_value() -> None:
    """A rejected value may be a secret; the stdlib's own messages quote it."""
    config = Config.from_mapping({"db": {"port": "s3cr3t", "mode": "s3cr3t", "level": "s3cr3t"}})
    with pytest.raises(BindError) as caught:
        config.bind(Db, prefix="db")
    reasons = [ln for ln in str(caught.value).splitlines() if "Reason:" in ln]
    assert reasons
    assert not any("s3cr3t" in line for line in reasons)


def test_errors_carry_the_origin() -> None:
    config = Config.from_mapping({"db": {"port": "nope"}})
    with pytest.raises(BindError, match="Origin:"):
        config.bind(Db, prefix="db")


def test_an_unknown_key_is_an_error_with_a_suggestion() -> None:
    """The most common configuration bug, and the one nobody else surfaces."""
    config = Config.from_mapping({"db": {"hostt": 1}})
    with pytest.raises(BindError, match=r"did you mean 'db\.host'"):
        config.bind(Db, prefix="db")


def test_strict_can_be_turned_off() -> None:
    config = Config.from_mapping({"db": {"host": "h", "extra": 1}})
    assert config.bind(Db, prefix="db", strict=False).host == "h"


def test_secret_fields_bind_and_stay_masked() -> None:
    config = Config.from_mapping({"db": {"password": "hunter2"}})
    db = config.bind(Db, prefix="db")
    assert str(db.password) == "********"
    assert db.password is not None
    with unlock_secrets():
        assert db.password.reveal() == "hunter2"


def test_choose_binder_rejects_an_unbindable_target() -> None:
    with pytest.raises(BindError, match="cannot bind"):
        choose_binder(int)


def test_dataclass_binder_lists_declared_paths() -> None:
    declared = DataclassBinder().declared(App)
    assert ("db", "host") in declared
    assert ("debug",) in declared


@pytest.mark.skipif(not pydantic_available(), reason="pydantic is not installed")
def test_binds_a_pydantic_model_and_maps_errors_onto_origins() -> None:
    import pydantic

    class PDb(pydantic.BaseModel):
        host: str = "localhost"
        port: pydantic.PositiveInt = 5432

    config = Config.from_mapping({"db": {"host": "h", "port": 1234}})
    assert config.bind(PDb, prefix="db").port == 1234

    bad = Config.from_mapping({"db": {"port": -1}})
    with pytest.raises(BindError) as caught:
        bad.bind(PDb, prefix="db")
    assert "db.port" in str(caught.value)
    assert "Origin:" in str(caught.value)


def test_relative_paths_resolve_against_the_file_that_declared_them(tmp_path: Path) -> None:
    """Only possible because the value knows its own origin."""
    from dataclasses import dataclass as dc

    from whence import Config, Discovery, RelativePath, load_settings, settings

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "myapp.toml").write_text('cert = "./ca.pem"\n', encoding="utf-8")

    @settings()
    @dc(frozen=True)
    class Tls:
        cert: RelativePath = field(default_factory=lambda: RelativePath("."))

    cfg = Config.load(
        discovery=Discovery(
            "myapp", path=(Path("deploy"),), user_config=False, secrets_dir=None, dotenv=()
        ),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    # Resolved against deploy/, not against the process working directory.
    assert load_settings(Tls, cfg).cert == deploy / "ca.pem"


def test_an_absolute_relative_path_is_left_alone() -> None:
    from whence import Origin, RelativePath

    got = RelativePath.resolve_against("/etc/ca.pem", Origin("file", "deploy/app.toml"))
    assert got == Path("/etc/ca.pem")


def test_a_path_from_a_source_with_no_file_resolves_against_the_cwd() -> None:
    from whence import Origin, RelativePath

    assert RelativePath.resolve_against("ca.pem", Origin("env", "APP_CERT")) == Path("ca.pem")
    assert RelativePath.resolve_against("ca.pem", None) == Path("ca.pem")


def test_annotated_fields_bind_to_the_type_they_declare() -> None:
    """`Annotated[int, ...]` is an int; the metadata belongs to whoever put it there.

    Nothing in whence writes this, but `@from_config` puts markers in exactly
    this position, and a schema shared with FastAPI or pydantic arrives carrying
    metadata of its own.
    """

    @dataclass(frozen=True, slots=True)
    class Http:
        retries: Annotated[int, "how many"] = 3
        region: Annotated[str | None, "aws"] = None

    bound = Config.from_mapping({"retries": "9", "region": ""}).bind(Http)
    assert bound.retries == 9
    assert bound.region is None
