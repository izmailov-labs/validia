"""Merging layers, and keeping the losers so `explain` has something to say."""

from whence import Layer, Origin, Tracked
from whence.tree import resolve, unflatten


def _layer(name: str, **values: object) -> Layer:
    origin = Origin(name, f"<{name}>")
    return Layer(name, {(k,): Tracked(v, origin) for k, v in values.items()})


def test_first_layer_wins_and_the_rest_are_recorded() -> None:
    merged = resolve([_layer("high", a=1), _layer("mid", a=2, b=3), _layer("low", a=4)])
    assert merged.values[("a",)] == 1
    assert merged.values[("b",)] == 3
    assert [name for name, _ in merged.shadowed[("a",)]] == ["mid", "low"]
    assert ("b",) not in merged.shadowed


def test_layers_that_found_nothing_are_still_listed() -> None:
    merged = resolve([_layer("high", a=1), Layer("absent", {}, found=False)])
    assert [layer.name for layer in merged.layers] == ["high", "absent"]
    assert merged.layers[1].found is False


def test_dotted_view() -> None:
    merged = resolve([_layer("only", a=1)])
    assert merged.dotted() == {"a": 1}
    assert list(merged.keys()) == [("a",)]


def test_unflatten_rebuilds_nesting() -> None:
    assert unflatten({("db", "host"): "h", ("db", "port"): 1, ("x",): 2}) == {
        "db": {"host": "h", "port": 1},
        "x": 2,
    }


def test_unflatten_lets_the_deeper_key_win_over_a_leaf() -> None:
    """`db=1` and `db.host=h` cannot both be true; the explicit deeper key wins."""
    assert unflatten({("db",): 1, ("db", "host"): "h"}) == {"db": {"host": "h"}}
