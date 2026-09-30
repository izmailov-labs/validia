"""Shared fixtures.

The autouse fixture below is the single most important piece of hygiene in a
test suite for a configuration library: without it, a maintainer who happens to
have `MYAPP_DB__HOST` exported in their shell gets different results than CI,
and the difference shows up as an unrelated test failing.
"""

import os
from collections.abc import Iterator
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def clean_env() -> Iterator[None]:
    """Run every test against a completely empty process environment."""
    with mock.patch.dict(os.environ, clear=True):
        yield
