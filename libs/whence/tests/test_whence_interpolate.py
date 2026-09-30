"""Placeholder expansion, over the merged tree rather than inside one file."""

import pytest

from whence import InterpolationError, Origin, Tracked
from whence.interpolate import interpolate
from whence.keys import canonical


def _values(**pairs: str) -> dict[tuple[str, ...], Tracked]:
    return {canonical(k): Tracked(v, Origin("file", "a.yaml")) for k, v in pairs.items()}


def test_reference_to_another_key() -> None:
    out = interpolate(_values(host="db.internal", url="postgres://${host}"), environ={})
    assert out[canonical("url")] == "postgres://db.internal"


def test_default_uses_posix_syntax_so_colons_are_unambiguous() -> None:
    out = interpolate(_values(url="${missing:-redis://h:6379}"), environ={})
    assert out[canonical("url")] == "redis://h:6379"


def test_optional_placeholder_removes_the_key_entirely() -> None:
    """HOCON's `${?VAR}`: the best override ergonomics in the field."""
    out = interpolate(_values(port="${?nothing.supplies.this}"), environ={})
    assert canonical("port") not in out


def test_an_optional_self_reference_is_not_a_cycle() -> None:
    """`port = ${?port}` is a natural thing to write and must not explode."""
    assert canonical("port") not in interpolate(_values(port="${?port}"), environ={})
    out = interpolate(_values(port="${port:-8080}"), environ={})
    assert out[canonical("port")] == "8080"


def test_optional_placeholder_resolves_when_present() -> None:
    out = interpolate(_values(a="8080", port="${?a}"), environ={})
    assert out[canonical("port")] == "8080"


def test_env_lookup_ignores_the_prefix() -> None:
    out = interpolate(_values(home="${env:HOME}"), environ={"HOME": "/home/u"})
    assert out[canonical("home")] == "/home/u"


def test_file_lookup_reads_a_container_secret(tmp_path: object) -> None:
    from pathlib import Path

    secret = Path(str(tmp_path)) / "pw"
    secret.write_text("s3cr3t\n", encoding="utf-8")
    out = interpolate(_values(pw=f"${{file:{secret}}}"), environ={})
    assert out[canonical("pw")] == "s3cr3t"


def test_missing_file_falls_through_to_the_default() -> None:
    out = interpolate(_values(pw="${file:/nope/nothing:-fallback}"), environ={})
    assert out[canonical("pw")] == "fallback"


def test_escaped_placeholder_stays_literal() -> None:
    out = interpolate(_values(literal=r"\${not.a.placeholder}"), environ={})
    assert out[canonical("literal")] == "${not.a.placeholder}"


def test_nested_reference_resolves_transitively() -> None:
    out = interpolate(_values(a="1", b="${a}", c="${b}"), environ={})
    assert out[canonical("c")] == "1"


def test_a_non_string_value_survives_a_whole_placeholder() -> None:
    values = _values(a="x")
    values[canonical("n")] = Tracked(5432, Origin("f", "a"))
    values[canonical("copy")] = Tracked("${n}", Origin("f", "a"))
    out = interpolate(values, environ={})
    assert out[canonical("copy")] == 5432


def test_unresolvable_placeholder_is_an_error_not_a_literal() -> None:
    """The behaviour that turns `${db.password}` reaching a driver into a failure."""
    with pytest.raises(InterpolationError, match="cannot resolve"):
        interpolate(_values(url="${nope}"), environ={})


def test_a_cycle_is_named() -> None:
    with pytest.raises(InterpolationError, match="circular"):
        interpolate(_values(a="${b}", b="${a}"), environ={})


def test_interpolated_values_record_their_derivation() -> None:
    out = interpolate(_values(host="h", url="${host}/x"), environ={})
    assert out[canonical("url")].origin.parent is not None


def test_values_without_placeholders_are_untouched() -> None:
    values = _values(plain="text")
    assert interpolate(values, environ={})[canonical("plain")] is values[canonical("plain")]
