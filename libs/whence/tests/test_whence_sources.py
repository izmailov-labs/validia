"""Sources, including the environment traps that differ per platform."""

from collections.abc import Iterator, MutableMapping
from pathlib import Path

import pytest

from whence import (
    ConfigError,
    DotEnvSource,
    EnvSource,
    FileSource,
    MappingSource,
    MissingConfigError,
    SecretsDirSource,
)
from whence.sources.dotenv import parse_dotenv


class WindowsEnviron(MutableMapping[str, str]):
    """``os.environ`` as it behaves on Windows: keys folded with ``str.upper()``.

    CPython folds them in ``os.py`` before any library code runs, so the original
    case of a variable name is already gone on Windows and cannot be recovered.
    Simulating it is the only way to prove from a Linux or macOS runner that
    whence's naming scheme survives the fold.
    """

    def __init__(self, data: dict[str, str]) -> None:
        self._data = {k.upper(): v for k, v in data.items()}

    def __getitem__(self, key: str) -> str:
        return self._data[key.upper()]

    def __setitem__(self, key: str, value: str) -> None:
        self._data[key.upper()] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key.upper()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def test_env_source_maps_only_prefixed_names() -> None:
    source = EnvSource("MYAPP_", environ={"MYAPP_DB__HOST": "h", "PATH": "/bin", "OTHER": "x"})
    layer = source.load()
    assert dict(layer.entries) == {("db", "host"): "h"}
    assert layer.entries[("db", "host")].origin.locator == "MYAPP_DB__HOST"


def test_env_source_never_falls_back_to_the_real_environment() -> None:
    """`environ or os.environ` would reinstate real secrets for `environ={}`."""
    layer = EnvSource("MYAPP_", environ={}).load()
    assert not layer.entries
    assert layer.found is False


def test_env_source_reads_file_indirection(tmp_path: Path) -> None:
    secret = tmp_path / "pw"
    secret.write_text("s3cr3t\n", encoding="utf-8")
    source = EnvSource("MYAPP_", environ={"MYAPP_DB__PASSWORD_FILE": str(secret)})
    layer = source.load()
    assert layer.entries[("db", "password")] == "s3cr3t"


def test_setting_both_var_and_var_file_is_an_error(tmp_path: Path) -> None:
    """Silently preferring one is how a rotated secret goes unnoticed."""
    secret = tmp_path / "pw"
    secret.write_text("from-file", encoding="utf-8")
    source = EnvSource(
        "MYAPP_",
        environ={"MYAPP_DB__PASSWORD": "inline", "MYAPP_DB__PASSWORD_FILE": str(secret)},
    )
    with pytest.raises(ConfigError, match="both"):
        source.load()


def test_file_indirection_reports_an_unreadable_target() -> None:
    source = EnvSource("MYAPP_", environ={"MYAPP_A_FILE": "/nope/nothing"})
    with pytest.raises(ConfigError, match="could not be read"):
        source.load()


def test_env_aliases_are_explicit_and_greppable() -> None:
    source = EnvSource(
        "MYAPP_", environ={"DATABASE_URL": "postgres://x"}, aliases={"DATABASE_URL": "db.url"}
    )
    layer = source.load()
    assert layer.entries[("db", "url")] == "postgres://x"


def test_env_source_names_the_variable_for_a_key() -> None:
    assert EnvSource("MYAPP_").name_for(("db", "host")) == "MYAPP_DB__HOST"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A=1", {"A": "1"}),
        ("export A=1", {"A": "1"}),
        ("A=", {"A": ""}),
        ("# c\nA=1", {"A": "1"}),
        ("A=1 # trailing", {"A": "1"}),
        ("A=#notacomment", {"A": "#notacomment"}),
        ("color=#ff0000", {"color": "#ff0000"}),  # regression: needs the space rule
        ('A="  spaced  "', {"A": "  spaced  "}),
        ("A='  spaced  '", {"A": "  spaced  "}),
        ('A="line\\nbreak"', {"A": "line\nbreak"}),
        ("A='literal\\n'", {"A": "literal\\n"}),
        ('A="has # hash"', {"A": "has # hash"}),
        ("A=`backtick`", {"A": "backtick"}),
        ("A=1\r\nB=2", {"A": "1", "B": "2"}),
        ("noequals", {}),
        ("=novalue", {}),
    ],
)
def test_dotenv_grammar(text: str, expected: dict[str, str]) -> None:
    assert {k: v.value for k, v in parse_dotenv(text, ".env").items()} == expected


def test_dotenv_multiline_values() -> None:
    parsed = parse_dotenv('KEY="line one\nline two"\nAFTER=1\n', ".env")
    assert parsed["KEY"] == "line one\nline two"
    assert parsed["AFTER"] == "1"


def test_dotenv_reports_positions() -> None:
    parsed = parse_dotenv("# c\nA=1\n", ".env")
    assert (parsed["A"].origin.line, parsed["A"].origin.column) == (2, 1)


def test_dotenv_rejects_an_unterminated_quote() -> None:
    from whence import FormatError

    with pytest.raises(FormatError, match="unterminated"):
        parse_dotenv('A="never closed\n', ".env")


def test_dotenv_source_reads_a_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MYAPP_DB__HOST=h\nUNRELATED=x\n", encoding="utf-8")
    layer = DotEnvSource(tmp_path / ".env", "MYAPP_").load()
    assert dict(layer.entries) == {("db", "host"): "h"}


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path) -> None:
    layer = DotEnvSource(tmp_path / "absent", "MYAPP_").load()
    assert layer.found is False


def test_secrets_dir_reads_one_file_per_key(tmp_path: Path) -> None:
    (tmp_path / "db.password").write_text("pw\n", encoding="utf-8")
    (tmp_path / "db__host").write_text("h", encoding="utf-8")
    (tmp_path / "..data").mkdir()  # the Kubernetes projection directory
    layer = SecretsDirSource(tmp_path).load()
    assert layer.entries[("db", "password")] == "pw"
    assert layer.entries[("db", "host")] == "h"


def test_a_missing_secrets_dir_is_not_an_error() -> None:
    """It never exists on Windows and rarely outside a container."""
    layer = SecretsDirSource("/nope/not/here").load()
    assert layer.found is False


def test_file_source_is_optional_by_default(tmp_path: Path) -> None:
    assert (FileSource(tmp_path / "absent.toml").load()).found is False


def test_a_required_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MissingConfigError):
        FileSource(tmp_path / "absent.toml", optional=False).load()


def test_file_source_stamps_the_profile(tmp_path: Path) -> None:
    path = tmp_path / "a.toml"
    path.write_text('host = "h"\n', encoding="utf-8")
    layer = FileSource(path, profile="prod").load()
    assert layer.entries[("host",)].origin.profile == "prod"


def test_mapping_source_accepts_nested_and_flat() -> None:
    flat_data: dict[tuple[str, ...], object] = {("db", "host"): "h"}
    nested = MappingSource({"db": {"host": "h"}}).load()
    flat = MappingSource(flat_data).load()
    assert dict(nested.entries) == dict(flat.entries)


def test_the_naming_scheme_survives_windows_case_folding() -> None:
    """On Windows CPython uppercases every key before library code runs.

    whence only ever looks for upper-case names, so `MYAPP_DB__MAX_RETRIES`
    folds losslessly and the same test passes on all three platforms. A scheme
    that inferred field names from the *case* of the variable could not.
    """
    for environ in (
        {"MYAPP_DB__MAX_RETRIES": "3"},
        WindowsEnviron({"myapp_db__max_retries": "3"}),
        WindowsEnviron({"MyApp_Db__Max_Retries": "3"}),
    ):
        layer = EnvSource("MYAPP_", environ=environ).load()
        assert dict(layer.entries) == {("db", "max_retries"): "3"}


def test_a_utf8_bom_does_not_end_up_in_the_first_key(tmp_path: Path) -> None:
    """PowerShell redirection and Windows editors write UTF-8 with a BOM."""
    from whence import FileSource

    path = tmp_path / "a.toml"
    path.write_bytes(b'\xef\xbb\xbf[db]\nhost = "h"\n')
    layer = FileSource(path).load()
    assert dict(layer.entries) == {("db", "host"): "h"}


def test_argv_source_reads_set_pairs() -> None:
    from whence import ArgvSource

    layer = ArgvSource(["--set", "db.host=h", "serve", "--set=db.port=1"]).load()
    assert layer.entries[("db", "host")] == "h"
    assert layer.entries[("db", "port")] == "1"
    assert layer.entries[("db", "host")].origin.locator == "--set db.host"


def test_argv_source_ignores_everything_else() -> None:
    """Whence is not an argument parser; owning a second bad one helps nobody."""
    from whence import ArgvSource

    layer = ArgvSource(["serve", "--verbose", "-x", "1"]).load()
    assert not layer.entries
    assert layer.found is False


def test_a_value_may_contain_equals_signs() -> None:
    from whence import ArgvSource

    layer = ArgvSource(["--set", "db.url=postgres://h/a?opt=1"]).load()
    assert layer.entries[("db", "url")] == "postgres://h/a?opt=1"


@pytest.mark.parametrize("argv", [["--set"], ["--set", "novalue"], ["--set", "=v"]])
def test_a_malformed_set_says_what_was_expected(argv: list[str]) -> None:
    from whence import ArgvSource

    with pytest.raises(ConfigError, match=r"db\.host=localhost"):
        ArgvSource(argv).load()
