"""Reading rule files: typed fields, rule definitions, extensions and examples."""

import difflib
import re
import tomllib
from collections.abc import Mapping, Sequence
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from .model import (
    _CONFIDENCES,
    _EXAMPLE_FIELDS,
    _RULE_FIELDS,
    _SCOPES,
    _T,
    _UNITS,
    SEVERITIES,
    Example,
    Rule,
    RuleError,
    Severity,
)


class _Reader:
    """Pulls typed fields out of parsed TOML, collecting problems instead of raising."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fields(self, table: Mapping[str, Any], where: str, allowed: Sequence[str]) -> None:
        for key in table:
            if key not in allowed:
                close = difflib.get_close_matches(key, list(allowed), n=1)
                hint = f" - did you mean {close[0]!r}?" if close else ""
                self.problems.append(f"{where}{key}: no such field{hint}")

    def text(
        self, table: Mapping[str, Any], key: str, where: str, *, required: bool = False
    ) -> str:
        value = table.get(key)
        if value is None:
            if required:
                self.problems.append(f"{where}{key}: missing")
            return ""
        if not isinstance(value, str) or (required and not value.strip()):
            self.problems.append(f"{where}{key}: expected a non-empty string")
            return ""
        return value

    def texts(self, table: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            self.problems.append(f"{where}{key}: expected a list of non-empty strings")
            return ()
        return tuple(value)

    def number(self, table: Mapping[str, Any], key: str, where: str) -> int:
        value = table.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.problems.append(f"{where}{key}: expected a whole number, 0 or more")
            return 0
        return value

    def flag(self, table: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
        value = table.get(key, default)
        if not isinstance(value, bool):
            self.problems.append(f"{where}{key}: expected true or false")
            return default
        return value

    def choice(
        self, table: Mapping[str, Any], key: str, where: str, options: tuple[_T, ...], default: _T
    ) -> _T:
        value = table.get(key, default)
        chosen = next((option for option in options if option == value), None)
        if chosen is None:
            self.problems.append(f"{where}{key}: {value!r} is not one of {list(options)}")
            return default
        return chosen

    def regex(self, value: str, where: str, flags: int) -> None:
        if not value:
            return
        try:
            re.compile(value, flags)
        except re.error as exc:
            self.problems.append(f"{where}: not a valid regular expression: {exc}")


def _load_toml(node: Traversable | Path, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(node.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RuleError(source, [f"not valid TOML: {exc}"]) from None


def _tables(reader: _Reader, data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    tables = data.get(key, [])
    if not isinstance(tables, list) or not all(isinstance(t, Mapping) for t in tables):
        reader.problems.append(f"{key}: expected [[{key}]] tables")
        return []
    return tables


def _header(
    reader: _Reader, data: Mapping[str, Any], *, required: bool = True
) -> tuple[str, tuple[str, ...]]:
    """Read the day a file was released, and what changed in it.

    A core file must say both; a project's may, and git keeps its history either way.
    """
    released = reader.text(data, "released", "", required=required)
    changes = reader.texts(data, "changes", "")
    if required and not changes:
        reader.problems.append("changes: say what changed in this release")
    return released, changes


def _build(
    reader: _Reader,
    table: Mapping[str, Any],
    cases: Mapping[str, Any],
    rule_id: str,
    category: str,
    *,
    titled: bool = False,
    everyone: bool = False,
) -> Rule:
    """Read one complete rule from its file's fields and its cases.

    A core rule needs a title. A default file is for every model, so it cannot name
    models: model severities belong in a vendor or model file.
    """
    reader.fields(table, "", _RULE_FIELDS)
    reader.fields(cases, "cases.", ("fires", "quiet"))
    named = reader.texts(table, "scope", "") or ("prompt",)
    scope = tuple(option for option in _SCOPES if option in named)
    unknown = [item for item in named if item not in _SCOPES]
    if unknown:
        reader.problems.append(f"scope: {unknown[0]!r} is not one of {list(_SCOPES)}")
    rule = Rule(
        id=rule_id,
        pattern=reader.text(table, "pattern", "", required=True),
        fix=reader.text(table, "fix", "", required=True),
        title=reader.text(table, "title", "", required=titled),
        severity=reader.choice(table, "severity", "", SEVERITIES, "warn"),
        scope=scope,
        case_sensitive=reader.flag(table, "case_sensitive", "", default=False),
        unless=reader.text(table, "unless", ""),
        run=reader.number(table, "run", ""),
        unit=reader.choice(table, "unit", "", _UNITS, "sentence"),
        at_least=reader.number(table, "at_least", ""),
        missing=reader.flag(table, "missing", "", default=False),
        models=reader.texts(table, "models", ""),
        otherwise=reader.choice(table, "otherwise", "", SEVERITIES, "info"),
        confidence=reader.choice(table, "confidence", "", _CONFIDENCES, "med"),
        judge=reader.text(table, "judge", ""),
        category=category,
        fires=reader.texts(cases, "fires", "cases."),
        quiet=reader.texts(cases, "quiet", "cases."),
    )
    flags = re.MULTILINE | (0 if rule.case_sensitive else re.IGNORECASE)
    reader.regex(rule.pattern, "pattern", flags)
    reader.regex(rule.unless, "unless", flags)
    shape = [
        name
        for name, on in (("run", rule.run), ("at_least", rule.at_least), ("missing", rule.missing))
        if on
    ]
    if len(shape) > 1:
        reader.problems.append(f"choose one of {', '.join(shape)}")
    if rule.unless and (rule.run or rule.missing):
        reader.problems.append("unless: only applies to a rule that fires per hit")
    if not rule.fires or not rule.quiet:
        reader.problems.append(
            "cases: every rule needs text it fires on and text it stays quiet on"
        )
    if rule.models and everyone:
        reader.problems.append(
            "models: a default file is for every model; put model severities in a vendor"
            " or model file"
        )
    return rule


def _as_table(rule: Rule) -> dict[str, Any]:
    """Write a rule back as the fields it would be read from, for ``extend``."""
    return {
        "title": rule.title,
        "pattern": rule.pattern,
        "fix": rule.fix,
        "severity": rule.severity,
        "scope": list(rule.scope),
        "case_sensitive": rule.case_sensitive,
        "unless": rule.unless,
        "run": rule.run,
        "unit": rule.unit,
        "at_least": rule.at_least,
        "missing": rule.missing,
        "models": list(rule.models),
        "otherwise": rule.otherwise,
        "confidence": rule.confidence,
        "judge": rule.judge,
    }


def _extend(
    reader: _Reader, body: Mapping[str, Any], cases: Mapping[str, Any], known: Rule
) -> Rule:
    """Lay some fields and extra cases over a rule, keeping everything else.

    The rule keeps every case it had and gains the new ones, so a rewritten pattern has
    to go on passing the cases the original was proved with.

    Severity reads the way it is written. ``severity`` alone sets it on every model,
    whatever models the rule named before. ``severity`` with ``models`` sets it on those
    models, and without ``otherwise`` every other model keeps what it had.
    """
    reader.fields(body, "", (*_RULE_FIELDS, "extend"))
    reader.fields(cases, "cases.", ("fires", "quiet"))
    merged = _as_table(known)
    if "severity" in body and "models" not in body:
        merged["models"] = []
    elif "models" in body and "otherwise" not in body and not known.models:
        merged["otherwise"] = known.severity
    merged.update({key: value for key, value in body.items() if key in _RULE_FIELDS})
    more = {
        "fires": [*known.fires, *reader.texts(cases, "fires", "cases.")],
        "quiet": [*known.quiet, *reader.texts(cases, "quiet", "cases.")],
    }
    return _build(reader, merged, more, known.id, known.category)


def _example(
    reader: _Reader,
    table: Mapping[str, Any],
    where: str,
    ids: set[str],
    category: str,
    model: str | None,
) -> Example:
    """Read one ``[[examples]]`` table, checking it names real rules.

    ``fires`` is a list of rule ids, or a table of rule ids to the severity each must
    have on the example's model.
    """
    reader.fields(table, where, _EXAMPLE_FIELDS)

    def full(name: str) -> str:
        """A rule as an example names it: by its name in the category, or its id."""
        if "/" not in name:
            return f"{category}/{name}"
        if not name.startswith(f"{category}/"):
            reader.problems.append(f"{where}fires: {name} is not a {category} rule")
        return name

    raw = table.get("fires", [])
    severities: dict[str, Severity] = {}
    if isinstance(raw, Mapping):
        for rule, value in raw.items():
            chosen = next((s for s in SEVERITIES if s == value), None)
            if chosen is None:
                reader.problems.append(
                    f"{where}fires.{rule}: {value!r} is not one of {list(SEVERITIES)}"
                )
            else:
                severities[full(rule)] = chosen
        fires = frozenset(full(rule) for rule in raw)
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        fires = frozenset(full(rule) for rule in raw)
    else:
        reader.problems.append(
            f"{where}fires: expected a list of rule names, or a table of them to severities"
        )
        fires = frozenset()
    counts = table.get("counts", {})
    if not isinstance(counts, Mapping) or not all(isinstance(n, int) for n in counts.values()):
        reader.problems.append(f"{where}counts: expected a table of rule names to numbers")
        counts = {}
    counts = {full(rule): number for rule, number in counts.items()}
    for name in sorted((fires | set(counts)) - ids):
        reader.problems.append(f"{where}fires: no rule {name!r}")
    return Example(
        name=reader.text(table, "name", where, required=True),
        text=reader.text(table, "text", where, required=True),
        fires=fires,
        severities=severities,
        counts=dict(counts),
        scope=reader.choice(table, "scope", where, _SCOPES, "prompt"),
        category=category,
        model=reader.text(table, "model", where) or model,
    )
