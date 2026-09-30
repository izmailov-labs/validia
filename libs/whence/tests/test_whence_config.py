"""The Config surface: reading, explaining, falling back, and the sync facade."""

from pathlib import Path

import pytest

from whence import Config, Discovery, MissingKeyError


def test_get_require_and_membership() -> None:
    config = Config.from_mapping({"db": {"host": "h", "port": "5432"}})
    assert config.get("db.host") == "h"
    assert config["db.host"] == "h"
    assert "db.host" in config
    assert "db.absent" not in config
    # The second positional argument is the default, as on `dict` and `os.environ`.
    assert config.get("db.absent", "fallback") == "fallback"
    assert config.get("db.absent", default="fallback") == "fallback"
    assert config.get("db.port", type_=int) == 5432
    assert config.require("db.port", type_=int) == 5432


def test_require_names_the_sources_it_consulted() -> None:
    config = Config.from_mapping({"a": 1})
    with pytest.raises(MissingKeyError, match="Consulted"):
        config.require("nope")


def test_origin_returns_where_a_value_came_from() -> None:
    config = Config.from_mapping({"a": 1})
    origin = config.origin("a")
    assert origin is not None
    assert origin.source == "overrides"
    assert config.origin("absent") is None


def test_with_fallback_fills_holes_without_replacing() -> None:
    library = Config.from_mapping({"a": "lib", "b": "lib"}, name="library")
    app = Config.from_mapping({"a": "app"}, name="app")
    merged = app.with_fallback(library)
    assert merged.get("a") == "app"
    assert merged.get("b") == "lib"


def test_repr_summarises_without_printing_values() -> None:
    text = repr(Config.from_mapping({"secret_token": "leak"}))
    assert "leak" not in text
    assert "1 keys" in text


def test_origins_and_dump_cover_every_key() -> None:
    config = Config.from_mapping({"a": 1, "b": 2})
    assert set(config.dump()) == {"a", "b"}
    assert set(config.origins()) == {"a", "b"}
    assert set(config.values) == {("a",), ("b",)}


def test_load_reads_every_source_together(tmp_path: Path) -> None:
    (tmp_path / "myapp.toml").write_text('[db]\nhost = "file"\nport = 1\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        environ={"MYAPP_DB__PORT": "2"},
        defaults={"db": {"timeout": 30}},
    )
    assert (config.get("db.host"), config.get("db.port"), config.get("db.timeout")) == (
        "file",
        "2",
        30,
    )


def test_interpolation_runs_across_sources(tmp_path: Path) -> None:
    """A base file may reference a key a higher source supplies."""
    (tmp_path / "myapp.toml").write_text('[db]\nurl = "postgres://${db.host}"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        environ={"MYAPP_DB__HOST": "from-env"},
    )
    assert config.get("db.url") == "postgres://from-env"


def test_expansion_can_be_disabled(tmp_path: Path) -> None:
    (tmp_path / "myapp.toml").write_text('a = "${nope}"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        environ={},
        expand=False,
    )
    assert config.get("a") == "${nope}"


def test_pyproject_table_is_the_lowest_file_layer(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.myapp]\na = "pyproject"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        environ={},
    )
    assert config.get("a") == "pyproject"


def test_a_missing_pyproject_table_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.other]\na = 1\n", encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None),
        cwd=tmp_path,
        environ={},
    )
    assert config.get("a") is None


def test_discovery_report_without_discovery() -> None:
    assert "no discovery" in Config.from_mapping({}).discovery_report()


def test_discovery_control_variables_do_not_become_data(tmp_path: Path) -> None:
    """`$MYAPP_CONFIG` says where to look; it is not a setting called `config`.

    Without excluding them, a schema that forbids unknown keys rejects its own
    discovery variables -- which is a confusing way to learn that you pointed
    `$MYAPP_CONFIG` at the right file.
    """
    named = tmp_path / "elsewhere.toml"
    named.write_text("a = 1\n", encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={"MYAPP_CONFIG": str(named), "MYAPP_PROFILES": "prod", "MYAPP_REAL": "kept"},
        argv=(),
    )
    assert config.get("a") == 1
    assert "config" not in config
    assert "profiles" not in config
    assert config.get("real") == "kept"


def test_aliases_map_a_foreign_variable_onto_a_key(tmp_path: Path) -> None:
    """An explicit, greppable table beats implicit name mangling."""
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={"DATABASE_URL": "postgres://x"},
        aliases={"DATABASE_URL": "db.url"},
        argv=(),
    )
    assert config.get("db.url") == "postgres://x"
