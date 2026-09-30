"""End-to-end, on whatever platform is actually running.

The rest of the suite tests the platform *seams* with fakes -- `user_config_dirs`
takes a platform argument so all three conventions can be asserted from one
runner. That is necessary but not sufficient: a fake cannot catch a filesystem
that folds case, a file the OS refuses to delete because we left a handle open,
or a default text encoding that is not UTF-8.

So this module does the opposite. It uses no fakes, asserts nothing about path
separators or absolute path strings, and **probes** for capabilities rather than
branching on `sys.platform`, so that the same assertions run on Linux, macOS and
Windows and mean the same thing on each.
"""

import sys
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from whence import (
    AmbiguousConfigError,
    Config,
    Discovery,
    Secret,
    load_settings,
    settings,
    unlock_secrets,
)
from whence._platform import current_platform, user_config_dirs

# --------------------------------------------------------------- capabilities


def _case_insensitive(directory: Path) -> bool:
    """Probe whether this directory folds case, the way CPython's own tests do.

    Per *directory*, not per platform: an APFS volume can be case-sensitive and a
    Linux runner can mount a case-insensitive share.
    """
    probe = directory / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return probe.samefile(directory / "caseprobe.tmp")
    except (OSError, ValueError):
        return False
    finally:
        probe.unlink()


def _filesystem_can_name(text: str) -> bool:
    """Probe whether a filename is expressible in this filesystem encoding.

    Under ``LANG=C`` with UTF-8 mode off, ``sys.getfilesystemencoding()`` is
    ASCII and the name cannot cross the syscall boundary at all. That is a
    property of the interpreter's environment, not of the library.
    """
    try:
        text.encode(sys.getfilesystemencoding())
    except UnicodeEncodeError:
        return False
    return True


def _usable_filename(directory: Path, name: str) -> bool:
    """Probe whether a name is a real file here, not a device or an error.

    Creation alone is not enough. Windows treats ``CON``, ``NUL`` and ``AUX`` as
    devices in *every* directory, so writing to ``secrets/con`` succeeds -- to
    the console -- and reading it back returns nothing. Only a full round-trip
    separates a usable filename from a silently discarded one.
    """
    target = directory / name
    try:
        target.write_text("round-trip", encoding="utf-8")
        ok = target.read_text(encoding="utf-8") == "round-trip"
        target.unlink()
    except OSError:
        return False
    return ok


# ------------------------------------------------------------ the full stack


@settings(prefix="db")
@dataclass(frozen=True, slots=True)
class Db:
    host: str = "localhost"
    port: int = 5432
    pool_size: int = 5
    url: str = ""
    password: Secret | None = None


@settings(prefix="http", strict=False)
@dataclass(frozen=True, slots=True)
class Http:
    timeout: timedelta = timedelta(seconds=30)
    retries: int = 3
    hosts: list[str] = field(default_factory=list)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A realistic layout: base file, profile overlay, .env, secrets directory."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "myapp.toml").write_text(
        'mode = "fast"\n'
        "[db]\n"
        'host = "localhost"\n'
        "port = 5432\n"
        "pool_size = 5\n"
        'url = "postgres://${db.host}:${db.port}/app"\n'
        "[http]\n"
        'timeout = "30s"\n'
        "retries = 3\n"
        'hosts = ["a.example", "b.example"]\n',
        encoding="utf-8",
    )
    (tmp_path / "config" / "myapp.prod.toml").write_text(
        '[db]\nhost = "db.internal"\npool_size = 20\n', encoding="utf-8"
    )
    (tmp_path / ".env").write_text("MYAPP_HTTP__RETRIES=7\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "db.password").write_text("hunter2\n", encoding="utf-8")
    return tmp_path


def _discovery(project: Path) -> Discovery:
    return Discovery(
        "myapp",
        path=(Path("config"),),
        user_config=False,
        secrets_dir=project / "secrets",
    )


def test_the_whole_stack_agrees_on_every_platform(project: Path) -> None:
    """One load exercising discovery, five sources, merge, interpolation and binding.

    Every assertion is platform-independent by construction, so a difference
    between runners is a real portability bug rather than a test artefact.
    """
    config = Config.load(
        discovery=_discovery(project),
        cwd=project,
        profiles=["prod"],
        environ={"MYAPP_DB__PORT": "6543"},
        argv=["--set", "db.pool_size=99"],
    )

    db = load_settings(Db, config)
    http = load_settings(Http, config)

    assert db.host == "db.internal"  # profile file over base file
    assert db.port == 6543  # environment over both
    assert db.pool_size == 99  # command line over everything
    assert db.url == "postgres://db.internal:6543/app"  # interpolated post-merge
    assert http.retries == 7  # .env over the base file
    assert http.timeout == timedelta(seconds=30)  # coerced from "30s"
    assert http.hosts == ["a.example", "b.example"]  # list survived as a list

    assert db.password is not None
    with unlock_secrets():
        assert db.password.reveal() == "hunter2"  # trailing newline stripped

    explanation = config.explain("db.host")
    assert "db.internal" in explanation
    assert "localhost" in explanation  # the shadowed value is still reported
    assert "profile=prod" in explanation


# ------------------------------------------------------------ encoding traps


def test_a_crlf_file_parses_the_same_as_lf(tmp_path: Path) -> None:
    """These files are routinely authored on Windows and read on Linux."""
    (tmp_path / "myapp.properties").write_bytes(
        b"# a comment\r\ndb.host = localhost\r\ndb.port = 5432\r\n"
    )
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("db.host") == "localhost"
    assert config.get("db.port", type_=int) == 5432


@pytest.mark.parametrize("suffix", ["toml", "json", "properties"])
def test_a_utf8_bom_never_reaches_the_first_key(tmp_path: Path, suffix: str) -> None:
    """PowerShell redirection and several Windows editors write a BOM."""
    bodies = {
        "toml": b'[db]\nhost = "localhost"\n',
        "json": b'{"db": {"host": "localhost"}}',
        "properties": b"db.host = localhost\n",
    }
    (tmp_path / f"myapp.{suffix}").write_bytes(b"\xef\xbb\xbf" + bodies[suffix])
    config = Config.load(
        discovery=Discovery(
            "myapp", formats=(suffix,), user_config=False, secrets_dir=None, dotenv=()
        ),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("db.host") == "localhost"
    assert "\ufeff" not in "".join(config.dump())


def test_non_ascii_values_survive_whatever_the_locale_encoding_is(
    tmp_path: Path,
) -> None:
    """Windows' default text encoding is a code page, not UTF-8.

    Every read whence performs passes `encoding=` explicitly; without that this
    raises `UnicodeDecodeError` on a Windows runner and nowhere else.
    """
    (tmp_path / "myapp.toml").write_text(
        'greeting = "café — naïve — 日本語 — Ω"\n', encoding="utf-8"
    )
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("greeting") == "café — naïve — 日本語 — Ω"


# ---------------------------------------------------------- filesystem traps


def _found_under(tmp_path: Path, *parts: str) -> object:
    """Put a config file at ``tmp_path/parts...`` and load it from there."""
    root = tmp_path.joinpath(*parts)
    root.mkdir(parents=True)
    (root / "myapp.toml").write_text("a = 1\n", encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", path=(root,), user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    return config.get("a")


def test_paths_with_spaces_are_found(tmp_path: Path) -> None:
    r"""`C:\Users\Ana Maria\My Project` is an ordinary path, not an edge case."""
    assert _found_under(tmp_path, "My Project", "sub dir") == 1


def test_paths_with_non_ascii_names_are_found(tmp_path: Path) -> None:
    """Skipped where the filesystem encoding cannot express the name.

    Probed, not assumed: this works on every platform in the CI matrix, and not
    under ``LANG=C`` with UTF-8 mode off -- where the failure is ``os.mkdir``
    raising ``UnicodeEncodeError`` before whence is involved at all.
    """
    name = "配置 dir"
    if not _filesystem_can_name(name):
        pytest.skip(f"filesystem encoding {sys.getfilesystemencoding()} cannot name {name!r}")
    assert _found_under(tmp_path, "My Project", name) == 1


def test_a_case_folding_filesystem_produces_no_phantom_ambiguity(
    tmp_path: Path,
) -> None:
    """On macOS and Windows `myapp.toml` and `MYAPP.TOML` are one file.

    Candidates are de-duplicated by `(st_dev, st_ino)` rather than by name,
    because `os.path.normcase` is the identity function on macOS -- precisely a
    platform whose filesystem folds case.
    """
    (tmp_path / "myapp.toml").write_text("a = 1\n", encoding="utf-8")
    folding = _case_insensitive(tmp_path)

    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("a") == 1

    if folding:
        # The same file reachable under a second spelling must not read as two.
        upper = Discovery("MYAPP", user_config=False, secrets_dir=None, dotenv=())
        plan = upper.plan(cwd=tmp_path)
        assert len(plan.files) <= 1


def test_two_real_formats_in_one_directory_still_raise(tmp_path: Path) -> None:
    """The ambiguity that matters must survive the de-duplication above."""
    (tmp_path / "myapp.toml").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "myapp.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AmbiguousConfigError):
        Config.load(
            discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
            cwd=tmp_path,
            environ={},
            argv=(),
        )


def test_no_file_handle_is_left_open(tmp_path: Path) -> None:
    """Windows refuses to delete or rename a file that is still open.

    A held handle would also stop the user's editor saving over it, so this is
    both a correctness and a usability property.
    """
    path = tmp_path / "myapp.toml"
    path.write_text("a = 1\n", encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("a") == 1

    replacement = tmp_path / "replacement.toml"
    replacement.write_text("a = 2\n", encoding="utf-8")
    replacement.replace(path)  # would raise PermissionError on Windows if held
    path.unlink()
    assert not path.exists()


def test_a_secrets_directory_tolerates_names_the_platform_allows(
    tmp_path: Path,
) -> None:
    """A key becomes a filename here, so the OS gets a say in what is legal.

    `con`, `nul` and `aux` are device names on Windows; the directory is simply
    read for whatever it contains rather than assuming any name is creatable.
    """
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "db.password").write_text("pw", encoding="utf-8")
    created = [name for name in ("con", "nul", "aux") if _usable_filename(secrets, name)]
    for name in created:
        (secrets / name).write_text("device-name", encoding="utf-8")

    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=secrets, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("db.password") == "pw"
    for name in created:
        assert config.get(name) == "device-name"


def test_an_absent_posix_only_source_is_not_an_error(tmp_path: Path) -> None:
    """`/run/secrets` and `/etc` do not exist on Windows.

    Gated on the directory existing, never on `sys.platform`, so the same code
    path runs everywhere. The assertion is deliberately about *not raising*:
    `/run/secrets` genuinely exists on some Linux hosts, and asserting it is
    empty would make this a test of the machine rather than of the library.
    """
    missing = tmp_path / "definitely" / "not" / "here"
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=missing, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.dump() == {}

    # And the real one, wherever this happens to run: present or not, it loads.
    Config.load(
        discovery=Discovery(
            "myapp", user_config=False, secrets_dir=Path("/run/secrets"), dotenv=()
        ),
        cwd=tmp_path,
        environ={},
        argv=(),
    )


def test_deeply_nested_paths_are_handled(tmp_path: Path) -> None:
    """Windows' MAX_PATH is 260 characters unless long paths are enabled."""
    root = tmp_path
    for i in range(8):
        root = root / f"level{i}"
    root.mkdir(parents=True)
    (root / "myapp.toml").write_text("a = 1\n", encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", path=(root,), user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("a") == 1


# ------------------------------------------------------ the real environment


def test_the_env_mapping_round_trips_through_the_real_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bijection, proved against `os.environ` rather than a fake.

    Windows folds variable names to upper case in CPython before any library
    code runs. whence only ever looks for upper-case names, so the fold is
    lossless -- but that claim is only worth anything when checked on the real
    mapping.
    """
    monkeypatch.setenv("MYAPP_DB__MAX_RETRIES", "9")
    monkeypatch.setenv("MYAPP_DB__HOST", "from-real-env")
    monkeypatch.delenv("MYAPP_CONFIG", raising=False)
    monkeypatch.delenv("MYAPP_PROFILES", raising=False)

    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
        cwd=tmp_path,
        argv=(),
    )
    assert config.get("db.max_retries") == "9"
    assert config.get("db.host") == "from-real-env"


def test_the_user_config_directory_is_sane_on_this_platform() -> None:
    """A thin real-OS check under the fakes, to catch a wiring mistake."""
    dirs = user_config_dirs("myapp")
    assert dirs
    assert all(d.is_absolute() for d in dirs)
    assert all(d.name == "myapp" for d in dirs)

    system = current_platform()
    joined = " ".join(str(d) for d in dirs)
    if system == "win32":
        assert "AppData" in joined
    elif system == "darwin":
        assert "Application Support" in joined
    else:
        assert ".config" in joined or "/etc/xdg" in joined


def test_temporary_directories_behave(tmp_path: Path) -> None:
    """Guards the probes above: if these break, the traps stop being tested."""
    assert _case_insensitive(tmp_path) in (True, False)
    assert _usable_filename(tmp_path, "ordinary-name") is True
    with tempfile.TemporaryDirectory() as d:
        assert Path(d).is_dir()
    assert sys.platform == current_platform()
