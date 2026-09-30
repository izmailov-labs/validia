"""Name-addressed insertion: what an ordinal-only model cannot express."""

import pytest

from whence import ConfigError, Layer, MappingSource, SourceChain


def _source(name: str, **values: object) -> MappingSource:
    return MappingSource(values, name=name)


def test_insertion_is_relative_to_a_name_not_an_index() -> None:
    chain = SourceChain([_source("env"), _source("files")])
    chain.add_before("files", _source("dotenv"))
    chain.add_after("files", _source("defaults"))
    assert chain.names() == ("env", "dotenv", "files", "defaults")


def test_add_first_and_last() -> None:
    chain = SourceChain([_source("middle")])
    chain.add_first(_source("top")).add_last(_source("bottom"))
    assert chain.names() == ("top", "middle", "bottom")


def test_replace_keeps_the_position() -> None:
    """How a source is decorated rather than displaced."""
    chain = SourceChain([_source("a"), _source("env"), _source("b")])
    removed = chain.replace("env", _source("env-decorated"))
    assert chain.names() == ("a", "env-decorated", "b")
    assert removed.name == "env"  # the displaced source is returned, not discarded


def test_remove_and_contains_and_len() -> None:
    chain = SourceChain([_source("a"), _source("b")])
    assert "a" in chain
    assert len(chain) == 2
    chain.remove("a")
    assert chain.names() == ("b",)
    assert list(chain)


def test_an_unknown_name_lists_what_is_present() -> None:
    chain = SourceChain([_source("env")])
    with pytest.raises(ConfigError, match="env"):
        chain.add_before("nope", _source("x"))


def test_ordinals_are_available_as_sugar() -> None:
    class Ranked(MappingSource):
        ordinal = 0

    high, low, mid = Ranked({}, name="high"), Ranked({}, name="low"), Ranked({}, name="mid")
    high.ordinal, low.ordinal, mid.ordinal = 400, 100, 250
    chain = SourceChain()
    for source in (high, low):
        chain.insert_by_ordinal(source, source.ordinal)
    chain.insert_by_ordinal(mid, mid.ordinal)
    assert chain.names() == ("high", "mid", "low")


def test_loading_merges_in_order() -> None:
    chain = SourceChain([_source("high", a=1), _source("low", a=2, b=3)])
    merged = chain.load()
    assert merged.values[("a",)] == 1
    assert merged.values[("b",)] == 3
    assert isinstance(merged.layers[0], Layer)
