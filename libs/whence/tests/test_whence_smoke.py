"""Packaging metadata and the public API contract."""

import whence


def test_version_is_exposed() -> None:
    assert isinstance(whence.__version__, str)
    assert whence.__version__


def test_public_api_is_declared_and_importable() -> None:
    assert len(whence.__all__) == len(set(whence.__all__))
    for name in whence.__all__:
        assert hasattr(whence, name), name


def test_the_core_imports_without_any_third_party_package() -> None:
    """The zero-dependency claim, checked at the import graph rather than in prose."""
    import subprocess
    import sys

    code = (
        "import sys, whence, whence.cli, whence.discovery, whence.binding;"
        "third = {m.split('.')[0] for m in sys.modules}"
        " & {'pydantic', 'yaml', 'defusedxml', 'dotenv', 'platformdirs'};"
        "print(sorted(third))"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert out.stdout.strip() == "[]"
