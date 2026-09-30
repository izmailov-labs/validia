"""The CLI, including the stdout/stderr discipline."""

from pathlib import Path

import pytest

from whence.cli import main


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "myapp.toml").write_text(
        '[db]\nhost = "localhost"\npassword = "leak"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MYAPP_CONFIG", raising=False)
    return tmp_path


def test_explain_prints_the_origin(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["myapp", "explain", "db.host"]) == 0
    assert "myapp.toml" in capsys.readouterr().out


def test_dump_redacts_and_writes_to_stdout(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["myapp", "dump"]) == 0
    captured = capsys.readouterr()
    assert "leak" not in captured.out
    assert "********" in captured.out
    assert captured.err == ""


def test_dump_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert main(["myapp", "dump", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["db.host"] == "localhost"


def test_reveal_is_opt_in(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["myapp", "dump", "--reveal"]) == 0
    assert "leak" in capsys.readouterr().out


def test_discovery_report(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["myapp", "discovery"]) == 0
    assert "$MYAPP_CONFIG" in capsys.readouterr().out


def test_explain_without_a_key_fails_on_stderr(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["myapp", "explain"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "needs a key" in captured.err


def test_errors_go_to_stderr_not_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dotenv wrote one line to stdout and broke a JSON-RPC transport."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MYAPP_CONFIG", "/definitely/not/here")
    assert main(["myapp", "dump"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "whence:" in captured.err


def test_profiles_can_be_repeated(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "myapp.prod.toml").write_text('[db]\nhost = "prod"\n', encoding="utf-8")
    assert main(["myapp", "explain", "db.host", "-p", "prod"]) == 0
    assert "prod" in capsys.readouterr().out
