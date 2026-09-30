"""@settings and @from_config: declaring configuration, and receiving it."""

from dataclasses import dataclass
from typing import Annotated

import pytest

from whence import (
    BindError,
    Config,
    ConfigError,
    Injected,
    MissingKeyError,
    Value,
    configure,
    current_config,
    from_config,
    load_settings,
    settings,
)


@settings("db")
@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432


CONFIG = Config.from_mapping({"db": {"host": "h", "port": 9999}, "retries": 7})


def test_the_markers_render_as_what_you_wrote() -> None:
    """The names appear in error messages, so they have to read as the API does."""
    assert repr(Injected) == "Injected"
    assert repr(Value("db.host")) == "Value(key='db.host')"


def test_settings_records_the_prefix() -> None:
    assert getattr(Db, "__whence_prefix__", None) == "db"


def test_settings_can_be_used_bare() -> None:
    """`@settings` and `@settings(...)` both work, as on `@dataclass`."""

    @settings
    @dataclass(frozen=True, slots=True)
    class Root:
        retries: int = 0

    assert getattr(Root, "__whence_prefix__", None) == ""
    assert Config.from_mapping({"retries": 7}).bind(Root).retries == 7


def test_bind_uses_the_declared_prefix_without_repeating_it() -> None:
    assert CONFIG.bind(Db).host == "h"


def test_load_from_an_explicit_config() -> None:
    assert load_settings(Db, CONFIG).port == 9999


def test_load_from_the_ambient_config() -> None:
    with current_config(CONFIG):
        assert load_settings(Db).host == "h"


def test_without_an_ambient_config_the_error_says_what_to_do() -> None:
    configure(None)
    with pytest.raises(ConfigError, match=r"whence\.configure"):
        load_settings(Db)


def test_from_config_fills_declared_parameters() -> None:
    @from_config
    def connect(
        *, db: Annotated[Db, Injected], retries: Annotated[int, Value("retries")] = 3
    ) -> str:
        return f"{db.host}:{db.port}/{retries}"

    with current_config(CONFIG):
        assert connect() == "h:9999/7"


def test_an_explicit_argument_always_wins() -> None:
    @from_config
    def connect(*, db: Annotated[Db, Injected]) -> str:
        return db.host

    with current_config(CONFIG):
        assert connect(db=Db(host="explicit")) == "explicit"


def test_from_config_refuses_a_parameter_that_is_not_keyword_only() -> None:
    """A positional argument is invisible to the filler, so reject it up front.

    `fill` skips a parameter only when it finds the name in `kwargs`. Passed
    positionally, the same argument sits in `args` instead, the parameter is
    filled a second time, and the call dies of a TypeError that names neither
    whence nor configuration. Decoration time is the only place this is cheap
    to say.
    """
    with pytest.raises(ConfigError, match="must be keyword-only"):

        @from_config
        def connect(db: Annotated[Db, Injected]) -> str:
            return db.host


def test_the_refusal_names_every_offending_parameter() -> None:
    with pytest.raises(ConfigError, match=r"'db', 'retries'"):

        @from_config
        def connect(db: Annotated[Db, Injected], retries: Annotated[int, Value("retries")]) -> str:
            return f"{db.host}/{retries}"


def test_a_positional_parameter_without_a_marker_is_still_fine() -> None:
    """The check covers marked parameters only; everything else is untouched."""

    @from_config
    def connect(host: str, *, retries: Annotated[int, Value("retries")] = 3) -> str:
        return f"{host}/{retries}"

    with current_config(CONFIG):
        assert connect("h") == "h/7"


def test_the_ambient_config_does_not_cross_into_another_thread() -> None:
    """`configure` writes to a ContextVar, and a new thread starts from an empty one.

    This is the shape of the bug: a worker pool that inherits nothing, failing
    only under concurrency. Pinned here because the fix is to pass the config
    explicitly across the boundary, not to reach for a global.
    """
    from concurrent.futures import ThreadPoolExecutor

    @from_config
    def connect(*, db: Annotated[Db, Injected]) -> str:
        return db.host

    configure(CONFIG)
    try:
        assert connect() == "h"
        with ThreadPoolExecutor(1) as pool, pytest.raises(ConfigError, match="no ambient"):
            pool.submit(connect).result()
    finally:
        configure(None)


def test_a_value_falls_back_to_the_parameters_own_default() -> None:
    """The default stays where Python puts defaults, so the function is callable."""

    @from_config
    def f(*, n: Annotated[int, Value("absent.key")] = 42) -> int:
        return n

    with current_config(CONFIG):
        assert f() == 42
    assert f(n=1) == 1  # no ambient config needed: nothing is left to fill


def test_a_value_with_no_default_is_required() -> None:
    @from_config
    def f(*, n: Annotated[int, Value("absent.key")]) -> int:
        return n

    with current_config(CONFIG), pytest.raises(MissingKeyError, match=r"absent\.key"):
        f()


def test_a_value_is_coerced_to_the_annotated_type() -> None:
    @from_config
    def f(*, n: Annotated[int, Value("n")]) -> int:
        return n

    with current_config(Config.from_mapping({"n": "12"})):
        assert f() == 12


def test_two_markers_on_one_parameter_is_an_error() -> None:
    with pytest.raises(ConfigError, match="more than one whence marker"):

        @from_config
        def f(*, n: Annotated[int, Value("a"), Value("b")] = 1) -> int:
            return n


def test_injected_with_a_default_is_rejected() -> None:
    """Injected always binds, so a default on it could never be reached."""
    with pytest.raises(ConfigError, match="can never be used"):

        @from_config
        def connect(*, db: Annotated[Db, Injected] = Db()) -> str:  # noqa: B008 - the point
            return db.host


def test_string_annotations_are_resolved() -> None:
    """A quoted annotation is what `from __future__ import annotations` produces."""

    @from_config
    def connect(*, db: "Annotated[Db, Injected]") -> str:
        return db.host

    with current_config(CONFIG):
        assert connect() == "h"


def test_an_unresolvable_return_annotation_does_not_block_decoration() -> None:
    """Only parameter annotations are resolved, and only when one is a string.

    ruff's TC rules push imports used solely in annotations behind
    `TYPE_CHECKING`, so a return type that does not exist at runtime is normal
    code rather than a curiosity. Resolving the whole signature would fail here
    for a reason having nothing to do with configuration.
    """

    @from_config
    def connect(*, db: Annotated[Db, Injected]) -> "OnlyUnderTypeChecking":  # type: ignore[name-defined] # noqa: F821
        return db.host

    with current_config(CONFIG):
        assert connect() == "h"


async def test_from_config_works_on_async_functions() -> None:
    @from_config
    async def connect(*, db: Annotated[Db, Injected]) -> str:
        return db.host

    with current_config(CONFIG):
        assert await connect() == "h"


def test_from_config_leaves_unmarked_functions_alone() -> None:
    @from_config
    def plain(a: int, b: int = 2) -> int:
        return a + b

    assert plain(1) == 3


def test_the_signature_survives_decoration() -> None:
    """The wrapper keeps the real parameters, so mypy sees through it."""
    import inspect

    @from_config
    def connect(*, db: Annotated[Db, Injected]) -> str:
        return db.host

    assert list(inspect.signature(connect).parameters) == ["db"]
    assert connect.__name__ == "connect"


def test_current_config_restores_the_previous_one() -> None:
    other = Config.from_mapping({"db": {"host": "other"}})
    with current_config(CONFIG), current_config(other):
        assert load_settings(Db).host == "other"
    configure(None)


def test_strictness_is_a_property_of_the_schema() -> None:
    """A class that deliberately covers part of a subtree says so once.

    Strict is the right default -- a typo'd key is otherwise invisible -- but
    without this, `@from_config` could only ever bind classes that declared every key
    under their prefix.
    """

    @settings("db")
    @dataclass(frozen=True, slots=True)
    class Whole:
        host: str = "localhost"

    @settings("db", strict=False)
    @dataclass(frozen=True, slots=True)
    class Part:
        host: str = "localhost"

    with pytest.raises(BindError, match="no such setting"):
        load_settings(Whole, CONFIG)
    assert load_settings(Part, CONFIG).host == "h"
    assert CONFIG.bind(Part).host == "h"
    assert load_settings(Whole, CONFIG, strict=False).host == "h"


async def test_from_config_honours_the_schema_strictness() -> None:
    @settings("db", strict=False)
    @dataclass(frozen=True, slots=True)
    class Part:
        host: str = "localhost"

    @from_config
    async def connect(*, db: Annotated[Part, Injected]) -> str:
        return db.host

    with current_config(CONFIG):
        assert await connect() == "h"
