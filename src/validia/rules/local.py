"""Write a project's own rule files: extend, replace, turn off, fall back, or add a rule.

Each function writes into the project's ``rules/`` folder, in the core's layout --
``rules/<category>/<name>/<target>/1.0.0.toml`` and its cases beside it -- then loads
the project and proves the rule on its target. If the project would no longer load,
or the rule's cases fail, nothing is left written: the files are removed and the
problems raised. So a front end -- the ``validia rules`` commands, a REST service --
never leaves a project in a state ``validia lint`` cannot read.
"""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from ..suites.authoring import toml_value
from . import (
    PROJECT_RULES,
    RuleCheck,
    RuleError,
    RulePack,
    Target,
    default_rules,
    is_pin,
    load_rules,
    verify_rules,
)

__all__ = [
    "Written",
    "disable_rule",
    "extend_rule",
    "fall_back",
    "new_rule",
    "replace_rule",
    "rule_target",
]

_FIELDS = (
    "title",
    "pattern",
    "unless",
    "scope",
    "case_sensitive",
    "run",
    "unit",
    "at_least",
    "missing",
    "severity",
    "models",
    "otherwise",
    "confidence",
    "judge",
    "fix",
)
_DEFAULTS: Mapping[str, Any] = {
    "title": "",
    "unless": "",
    "scope": ["prompt"],
    "case_sensitive": False,
    "run": 0,
    "unit": "sentence",
    "at_least": 0,
    "missing": False,
    "severity": "warn",
    "models": [],
    "otherwise": "info",
    "confidence": "med",
    "judge": "",
}


@dataclass(frozen=True, slots=True)
class Written:
    """What a write left in the project, and the proof that it holds.

    Attributes:
        paths: The files written, the rule's first, then its cases if any.
        pack: The project's rules for the target, as read after the write.
        checks: The rule's own check, and every example that names it.
    """

    paths: tuple[Path, ...]
    pack: RulePack
    checks: tuple[RuleCheck, ...]


def rule_target(target: str) -> Target | None:
    """Turn a target folder into the model it is read for.

    Args:
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.

    Returns:
        ``None`` for the default; otherwise ``(provider, model)``, where a vendor's
        default reads as the model ``default`` of that vendor.

    Raises:
        RuleError: If the target is not one of those shapes.
    """
    if target == "default":
        return None
    provider, slash, model = target.partition("/")
    if not slash or not provider or not model or "/" in model:
        raise RuleError(
            "the target",
            [f"{target!r}: expected default, <provider>/default or <provider>/<model>"],
        )
    return (provider, model)


def _split(rule_id: str) -> tuple[str, str]:
    category, slash, name = rule_id.partition("/")
    if not slash or not category or not name:
        raise RuleError(
            "the rule", [f"{rule_id!r}: name a rule by its id, as in reasoning/show-reasoning"]
        )
    return category, name


def _pack(
    project: Path,
    target: str,
    pins: Mapping[str, str] | None,
    folder: Traversable | Path | None,
) -> RulePack:
    model = rule_target(target)
    if project.is_dir():
        return load_rules(project, target=model, pins=pins, folder=folder)
    return default_rules(model, pins=pins, folder=folder)


def _known(pack: RulePack, rule_id: str) -> None:
    """Refuse a rule the target does not have, naming the closest one."""
    _split(rule_id)
    ids = [rule.id for rule in pack.rules]
    if rule_id in ids:
        return
    name = rule_id.partition("/")[2]
    close = [other for other in ids if other.partition("/")[2] == name]
    hint = f" - did you mean {close[0]}?" if close else ""
    raise RuleError("the rule", [f"no rule {rule_id} for these rules ({pack.version}){hint}"])


def _newest(project: Path, rule_id: str, target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The newest file in a target's folder, and its cases; empty when there is none."""
    category, name = _split(rule_id)
    folder = project.joinpath(category, name, *target.split("/"))
    versions = sorted(
        (tuple(int(part) for part in stem.split(".")), stem)
        for entry in (folder.iterdir() if folder.is_dir() else ())
        if entry.name.endswith(".toml")
        and not entry.name.endswith(".cases.toml")
        and is_pin(stem := entry.name.removesuffix(".toml"))
        and stem.count(".") == 2
    )
    if not versions:
        return {}, {}
    newest = versions[-1][1]
    body = tomllib.loads((folder / f"{newest}.toml").read_text(encoding="utf-8"))
    cases_file = folder / f"{newest}.cases.toml"
    cases = tomllib.loads(cases_file.read_text(encoding="utf-8")) if cases_file.is_file() else {}
    return {k: v for k, v in body.items() if k not in ("released", "changes")}, cases


def _place(project: Path, rule_id: str, target: str, version: str | None) -> Path:
    """The file to write: the next free version in the target's folder, or the one asked for."""
    category, name = _split(rule_id)
    folder = project.joinpath(category, name, *target.split("/"))
    taken = sorted(
        entry.name.removesuffix(".toml")
        for entry in (folder.iterdir() if folder.is_dir() else ())
        if entry.name.endswith(".toml") and not entry.name.endswith(".cases.toml")
    )
    if version is not None:
        if not is_pin(version) or version.count(".") != 2:
            raise RuleError("the version", [f"{version!r}: expected a version, as in 1.1.0"])
        if version in taken:
            raise RuleError(_shown(project, folder / f"{version}.toml"), ["already exists"])
        return folder / f"{version}.toml"
    if taken:
        newest = taken[-1]
        major, minor, _ = (int(part) for part in newest.split("."))
        raise RuleError(
            _shown(project, folder / f"{newest}.toml"),
            [f"already exists: edit it, or write a newer one with version {major}.{minor + 1}.0"],
        )
    return folder / "1.0.0.toml"


def _shown(project: Path, path: Path) -> str:
    return f"{PROJECT_RULES}/{path.relative_to(project).as_posix()}"


def _lines(key: str, values: Sequence[Any]) -> str:
    """One TOML key, a list on one line when short, one item a line when not."""
    inline = f"{key} = {toml_value(list(values))}"
    if len(inline) <= 88 and not any("\n" in str(value) for value in values):
        return inline + "\n"
    items = "".join(f"  {toml_value(value)},\n" for value in values)
    return f"{key} = [\n{items}]\n"


def _rule_text(heading: str, fields: Mapping[str, Any]) -> str:
    body = "".join(
        _lines(key, value) if isinstance(value, list | tuple) else f"{key} = {toml_value(value)}\n"
        for key, value in fields.items()
    )
    return f"# {heading}\n\n{body}"


def _cases_text(rule_id: str, fires: Sequence[str], quiet: Sequence[str]) -> str:
    text = f"# Cases for {rule_id}: text it must fire on, and text it must stay quiet on.\n\n"
    if fires:
        text += _lines("fires", fires)
    if quiet:
        text += _lines("quiet", quiet)
    return text


def _write(
    project: Path,
    rule_id: str,
    target: str,
    rule: str,
    cases: str | None,
    version: str | None,
    pins: Mapping[str, str] | None,
    folder: Traversable | Path | None,
) -> Written:
    """Write a rule file and its cases, keep them only if the project loads and proves."""
    path = _place(project, rule_id, target, version)
    made = [parent for parent in reversed(path.parents) if not parent.exists()]
    path.parent.mkdir(parents=True, exist_ok=True)
    paths = [path]
    path.write_text(rule, encoding="utf-8")
    if cases is not None:
        paths.append(path.with_name(f"{path.stem}.cases.toml"))
        paths[-1].write_text(cases, encoding="utf-8")
    try:
        pack = _pack(project, target, pins, folder)
        category = rule_id.partition("/")[0]
        ids = {rule.id for rule in pack.rules}
        checks = sorted(
            (
                check
                for check in verify_rules(pack)
                if check.name == rule_id or (check.category == category and check.name not in ids)
            ),
            key=lambda check: check.name != rule_id,
        )
        failures = [f"{check.name}: {failure}" for check in checks for failure in check.failures]
        if failures:
            raise RuleError(_shown(project, path), failures)
    except RuleError:
        for written in paths:
            written.unlink(missing_ok=True)
        for parent in reversed(made):
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        raise
    return Written(tuple(paths), pack, tuple(checks))


def extend_rule(
    project: Path,
    rule_id: str,
    *,
    target: str = "default",
    fields: Mapping[str, Any] | None = None,
    fires: Sequence[str] = (),
    quiet: Sequence[str] = (),
    version: str | None = None,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> Written:
    """Extend a rule for a target: change some fields, add cases, keep the rest.

    Args:
        project: The project's ``rules/`` folder; made when missing.
        rule_id: The rule, as ``reasoning/show-reasoning``.
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.
        fields: The fields to change, as a rule file names them (``severity``, ``fix``...).
        fires: More text the rule must fire on.
        quiet: More text the rule must stay quiet on.
        version: The file's version; the first free one, ``1.0.0``, when omitted. A newer
            version starts from the folder's newest file -- its fields and its cases --
            and lays these changes over it, since it takes that file's place.
        pins: Category names to the core release to read.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        What was written, and its proof.

    Raises:
        RuleError: If there is nothing to change, the rule is unknown, the file
            exists, or the result does not load or prove; nothing is left written.
    """
    changes = dict(fields or {})
    if not changes and not fires and not quiet:
        raise RuleError(
            "the extension", ["say what to change: a field, or cases it fires or stays quiet on"]
        )
    _known(_pack(project, target, pins, folder), rule_id)
    # A newer file takes the older one's place, so it starts from what the older one said.
    older, older_cases = _newest(project, rule_id, target) if version is not None else ({}, {})
    if "enabled" in older or "from" in older:
        raise RuleError(
            "the extension",
            [
                f"the newest file in that folder says {next(iter(older))} = ...; replace the rule instead"
            ],
        )
    body = {"extend": True, **older, **changes} if "pattern" not in older else {**older, **changes}
    fires = [*older_cases.get("fires", []), *fires]
    quiet = [*older_cases.get("quiet", []), *quiet]
    heading = "extended" if body.get("extend") else "replaces it"
    rule = _rule_text(f"{rule_id} on {target}: {heading}.", body)
    cases = _cases_text(rule_id, fires, quiet) if fires or quiet else None
    return _write(project, rule_id, target, rule, cases, version, pins, folder)


def replace_rule(
    project: Path,
    rule_id: str,
    *,
    target: str = "default",
    version: str | None = None,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> Written:
    """Copy a rule, as the target reads it now, into a file of its own to edit.

    The copy is a full definition, so it replaces the rule from the target up: every
    field that differs from the defaults, and every case the rule has.

    Args:
        project: The project's ``rules/`` folder; made when missing.
        rule_id: The rule, as ``reasoning/show-reasoning``.
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.
        version: The file's version; the first free one, ``1.0.0``, when omitted.
        pins: Category names to the core release to read.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        What was written, and its proof.

    Raises:
        RuleError: If the rule is unknown, the file exists, or the copy does not
            load or prove; nothing is left written.
    """
    pack = _pack(project, target, pins, folder)
    _known(pack, rule_id)
    rule = next(item for item in pack.rules if item.id == rule_id)
    values = {
        "title": rule.title,
        "pattern": rule.pattern,
        "unless": rule.unless,
        "scope": list(rule.scope),
        "case_sensitive": rule.case_sensitive,
        "run": rule.run,
        "unit": rule.unit,
        "at_least": rule.at_least,
        "missing": rule.missing,
        "severity": rule.severity,
        "models": list(rule.models),
        "otherwise": rule.otherwise,
        "confidence": rule.confidence,
        "judge": rule.judge,
        "fix": rule.fix,
    }
    kept = {
        key: value
        for key, value in values.items()
        if key in ("pattern", "fix", "severity") or value != _DEFAULTS.get(key)
    }
    if not rule.models:
        kept.pop("otherwise", None)
    heading = f"{rule_id} on {target}: replaces it, copied from {' -> '.join(rule.origin)}."
    text = _rule_text(heading, kept)
    cases = _cases_text(rule_id, rule.fires, rule.quiet)
    return _write(project, rule_id, target, text, cases, version, pins, folder)


def disable_rule(
    project: Path,
    rule_id: str,
    *,
    target: str = "default",
    version: str | None = None,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> Written:
    """Turn a rule off for a target and everything read after it.

    Args:
        project: The project's ``rules/`` folder; made when missing.
        rule_id: The rule, as ``reasoning/show-reasoning``.
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.
        version: The file's version; the first free one, ``1.0.0``, when omitted.
        pins: Category names to the core release to read.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        What was written; it proves nothing of the rule, which is gone.

    Raises:
        RuleError: If the rule is unknown, or the file exists; nothing is left written.
    """
    _known(_pack(project, target, pins, folder), rule_id)
    rule = _rule_text(f"{rule_id} on {target}: turned off.", {"enabled": False})
    return _write(project, rule_id, target, rule, None, version, pins, folder)


def fall_back(
    project: Path,
    rule_id: str,
    release: str,
    *,
    version: str | None = None,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> Written:
    """Take one core rule, along its whole chain, as an older release had it.

    Args:
        project: The project's ``rules/`` folder; made when missing.
        rule_id: The rule, as ``reasoning/show-reasoning``.
        release: The core release, as ``1`` or ``1.2.0``.
        version: The file's version; the first free one, ``1.0.0``, when omitted.
        pins: Category names to the core release to read for everything else.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        What was written, and its proof.

    Raises:
        RuleError: If the release or rule is unknown, or the file exists; nothing is
            left written.
    """
    rule = _rule_text(f"{rule_id}: as core release {release} had it.", {"from": release})
    return _write(project, rule_id, "default", rule, None, version, pins, folder)


def new_rule(
    project: Path,
    rule_id: str,
    *,
    fields: Mapping[str, Any],
    fires: Sequence[str],
    quiet: Sequence[str],
    target: str = "default",
    version: str | None = None,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> Written:
    """Add a rule of the project's own, in a core category or one of its own.

    Args:
        project: The project's ``rules/`` folder; made when missing.
        rule_id: The new rule, as ``brand/sorry``.
        fields: Its fields; ``pattern`` and ``fix`` are required.
        fires: Text it must fire on; at least one.
        quiet: Text it must stay quiet on; at least one.
        target: ``default`` for every model, or a vendor's or model's folder for a
            rule only those models have.
        version: The file's version; the first free one, ``1.0.0``, when omitted.
        pins: Category names to the core release to read.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        What was written, and its proof.

    Raises:
        RuleError: If the rule exists already, a field is missing or wrong, or its
            cases fail; nothing is left written.
    """
    _split(rule_id)
    if rule_id in {rule.id for rule in _pack(project, target, pins, folder).rules}:
        raise RuleError(
            "the rule", [f"{rule_id} exists already: extend it, or replace it, instead"]
        )
    ordered = {key: fields[key] for key in _FIELDS if key in fields}
    ordered.update({key: value for key, value in fields.items() if key not in ordered})
    rule = _rule_text(f"{rule_id}: a rule of this project's own.", ordered)
    cases = _cases_text(rule_id, fires, quiet)
    return _write(project, rule_id, target, rule, cases, version, pins, folder)
