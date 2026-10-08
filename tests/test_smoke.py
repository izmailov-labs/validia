"""Scaffolding smoke tests: packaging metadata and the async test plumbing."""

import asyncio

import validia


def test_version_is_exposed() -> None:
    assert isinstance(validia.__version__, str)
    assert validia.__version__


def test_public_api_is_declared() -> None:
    assert "__version__" in validia.__all__


async def test_async_tests_actually_run() -> None:
    """Prove pytest-asyncio auto mode is wired up before any real async code lands."""
    await asyncio.sleep(0)
    assert asyncio.get_running_loop().is_running()
