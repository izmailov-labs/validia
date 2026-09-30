"""Every format, and the exact positions the ones that can report them give."""

import pytest

from whence import FormatError
from whence.formats import BUILTIN, loader_for, registry, suffixes
from whence.formats.json import load_json
from whence.formats.properties import load_properties
from whence.formats.toml import load_toml
from whence.formats.xml import load_xml
from whence.formats.yaml import load_yaml

SAME = {("db", "host"): "localhost", ("db", "port"): 5432}


def test_every_format_reaches_the_same_result() -> None:
    """Round-trip: the loaders cannot drift apart without this failing."""
    loaded = {
        "toml": load_toml('[db]\nhost = "localhost"\nport = 5432\n', "a.toml"),
        "json": load_json('{"db": {"host": "localhost", "port": 5432}}', "a.json"),
        "yaml": load_yaml("db:\n  host: localhost\n  port: 5432\n", "a.yaml"),
        "xml": load_xml("<c><db><host>localhost</host><port>5432</port></db></c>", "a.xml"),
        "properties": load_properties("db.host=localhost\ndb.port=5432\n", "a.properties"),
    }
    for name, result in loaded.items():
        as_text = {k: str(v.value) for k, v in result.items()}
        assert as_text == {k: str(v) for k, v in SAME.items()}, name


def test_yaml_reports_line_and_column() -> None:
    result = load_yaml("db:\n  host: localhost\n  port: 5432\n", "a.yaml")
    origin = result[("db", "host")].origin
    assert (origin.line, origin.column) == (2, 9)


def test_properties_reports_line_numbers() -> None:
    result = load_properties("# comment\ndb.host = localhost\n", "a.properties")
    assert result[("db", "host")].origin.line == 2


def test_toml_and_json_report_file_level_origins_only() -> None:
    """Documented limitation: tomllib and json expose no positions."""
    for result in (load_toml("a = 1\n", "a.toml"), load_json('{"a": 1}', "a.json")):
        assert next(iter(result.values())).origin.line is None


def test_properties_handles_java_rules() -> None:
    text = "! bang comment\n# hash comment\na:1\nb 2\nc\\:d = 3\nlong = one\\\n  two\nu = \\u00e9\n"
    result = load_properties(text, "a.properties")
    assert result[("a",)] == "1"
    assert result[("b",)] == "2"
    assert result[("c:d",)] == "3"
    assert result[("long",)] == "onetwo"
    assert result[("u",)] == "\u00e9"


def test_properties_handles_crlf() -> None:
    """These files are routinely authored on Windows."""
    result = load_properties("a=1\r\nb=2\r\n", "a.properties")
    assert result[("a",)] == "1"
    assert result[("b",)] == "2"


def test_properties_rejects_a_malformed_unicode_escape() -> None:
    with pytest.raises(FormatError, match="malformed"):
        load_properties("a = \\u00zz\n", "a.properties")


def test_xml_reads_the_java_properties_dtd_shape() -> None:
    text = '<properties><entry key="db.host">h</entry><entry>ignored</entry></properties>'
    assert load_xml(text, "a.xml") == {("db", "host"): "h"}


def test_xml_reads_attributes_and_named_entries() -> None:
    text = '<c><db host="h"/><pool name="main"><size>4</size></pool></c>'
    result = load_xml(text, "a.xml")
    assert result[("db", "host")] == "h"
    assert result[("pool", "main", "size")] == "4"


def test_xml_rejects_a_dtd() -> None:
    with pytest.raises(FormatError, match="DTD"):
        load_xml('<!DOCTYPE x [<!ENTITY e "v">]><x>&e;</x>', "a.xml")


def test_a_real_billion_laughs_document_is_stopped() -> None:
    """Expat >=2.4.1 bounds amplification, which is why defusedxml is not needed."""
    entities = "".join(
        f'<!ENTITY e{i} "{f"&e{i - 1};" * 10}">' if i else '<!ENTITY e0 "AAAAAAAAAA">'
        for i in range(10)
    )
    with pytest.raises(FormatError):
        load_xml(f"<?xml version='1.0'?><!DOCTYPE r [{entities}]><r>&e9;</r>", "bomb.xml")


@pytest.mark.parametrize(
    ("loader", "text"),
    [
        (load_toml, "= nope"),
        (load_json, "{nope}"),
        (load_yaml, "a:\n- b\n  c: d"),
        (load_xml, "<unclosed>"),
    ],
)
def test_parse_failures_name_the_file(loader: object, text: str) -> None:
    with pytest.raises(FormatError, match=r"bad\."):
        loader(text, "bad.cfg")  # type: ignore[operator]


@pytest.mark.parametrize(("loader", "text"), [(load_json, "[1,2]"), (load_yaml, "- 1\n- 2\n")])
def test_top_level_must_be_a_mapping(loader: object, text: str) -> None:
    with pytest.raises(FormatError, match="top level"):
        loader(text, "a.cfg")  # type: ignore[operator]


def test_empty_yaml_is_empty_not_an_error() -> None:
    assert load_yaml("", "a.yaml") == {}


def test_registry_lookup() -> None:
    assert loader_for(".TOML") is BUILTIN["toml"]
    assert "toml" in suffixes()
    assert "conf" in registry({".conf": load_toml})
    with pytest.raises(FormatError, match="no loader"):
        loader_for("nope")
