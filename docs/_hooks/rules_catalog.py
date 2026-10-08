"""Write the rule catalog on the prompt rules page from the rules validia ships.

mkdocs runs this file as a hook (``hooks:`` in ``mkdocs.yml``). The page holds a
marker, and every build replaces it with tables read through ``validia.rules``, so
the catalog lists the rules in the installed package and cannot drift from them.
"""

from collections.abc import Mapping

from validia.rules import Rule, RulePack, core_categories, core_releases, verify_core

MARKER = "<!-- rules-catalog -->"


_READS = {"prompt": "prompt", "tool_description": "tool descriptions"}


def on_page_markdown(markdown: str, **_: object) -> str:
    """Replace the catalog marker, on whichever page holds it, with the catalog.

    Args:
        markdown: The page's Markdown source.
        **_: The page, config and files mkdocs passes; unused.

    Returns:
        The page, with the marker replaced.
    """
    if MARKER not in markdown:
        return markdown
    return markdown.replace(MARKER, catalog())


def catalog() -> str:
    """Describe every core target, then every rule, category by category.

    Returns:
        Markdown: a table of targets, then a section per category.
    """
    # verify_core reads each target along its own chain, exactly as lint would, and
    # hands back the pack it read; the checks it also runs are the test suite's job.
    packs: dict[str, dict[str, RulePack]] = {}
    for category, target, pack, _ in verify_core():
        packs.setdefault(category, {})[target] = pack
    sections = [_targets(packs)]
    sections += [_category(category, packs.get(category, {})) for category in core_categories()]
    return "\n\n".join(sections)


def _changed_at(target: str, rule: Rule) -> bool:
    """Whether the last file laid over a rule is the target's own."""
    return rule.origin[-1].startswith(f"{target} ")


def _parent(target: str, packs: Mapping[str, RulePack]) -> RulePack | None:
    """The pack a target's files are laid over: its vendor's, else the default."""
    provider, _, model = target.partition("/")
    if model != "default" and f"{provider}/default" in packs:
        return packs[f"{provider}/default"]
    return packs.get("default")


def _reading_order(target: str) -> tuple[bool, str, bool, str]:
    """Sort targets as lint reads them: the default, then by vendor, its default first."""
    provider, _, model = target.partition("/")
    return (target != "default", provider, model != "default", model)


def _read_for(target: str) -> str:
    provider, _, model = target.partition("/")
    if target == "default":
        return "every model"
    if model == "default":
        return f"every `{provider}:` model"
    return f"`{provider}:{model}` only"


def _targets(packs: Mapping[str, Mapping[str, RulePack]]) -> str:
    """A table of every target folder the core has, and what its files do."""
    changes: dict[str, list[str]] = {}
    guidance: dict[str, list[str]] = {}
    for category, by_target in packs.items():
        for target, pack in by_target.items():
            changes.setdefault(target, [])
            guidance.setdefault(target, [])
            if target != "default":
                changes[target] += [rule.id for rule in pack.rules if _changed_at(target, rule)]
            if any(note.target == target for note in pack.guidance):
                guidance[target].append(category)
    order = sorted(changes, key=_reading_order)
    rows = [
        "| Target | Read for | Rules it changes | Guidance for |",
        "|---|---|---|---|",
    ]
    total = sum(
        len(by_target["default"].rules) for by_target in packs.values() if "default" in by_target
    )
    for target in order:
        if target == "default":
            changed = f"defines all {total}"
        else:
            changed = ", ".join(f"`{rule}`" for rule in changes[target]) or "—"
        notes = ", ".join(guidance[target]) or "—"
        rows.append(f"| `{target}` | {_read_for(target)} | {changed} | {notes} |")
    return "\n".join(rows)


def _severity(rule: Rule) -> str:
    """A rule's severity across the models its file reaches."""
    if not rule.models:
        return rule.severity
    named = ", ".join(f"`{glob}`" for glob in rule.models)
    return f"{rule.severity} on {named}; {rule.otherwise} on the rest"


def _change(target: str, rule: Rule, base: Rule | None) -> str:
    """What one target's file did to a rule, in a few words."""
    last = rule.origin[-1].split(" ")
    kind = last[2] if len(last) > 2 else "defines"
    if base is None or kind == "replace":
        lead = "replaced" if kind == "replace" else "defined here"
        return f"`{target}`: {lead}, {_severity(rule)}"
    said = [_severity(rule)] if _severity(rule) != _severity(base) else []
    if rule.pattern != base.pattern:
        said.append("own pattern")
    if rule.fix != base.fix:
        said.append("own fix")
    return f"`{target}`: {', '.join(said) or 'more cases'}"


def _category(category: str, packs: Mapping[str, RulePack]) -> str:
    """A category's latest release, its guidance targets and a table of its rules."""
    default = packs.get("default")
    base = {rule.id: rule for rule in default.rules} if default is not None else {}
    order = list(base)
    found: dict[str, Rule] = dict(base)
    notes: dict[str, list[str]] = {}
    for target, pack in packs.items():
        if target == "default":
            continue
        here = {rule.id: rule for rule in pack.rules}
        for rule in pack.rules:
            if not _changed_at(target, rule):
                continue
            if rule.id not in found:
                found[rule.id] = rule
                order.append(rule.id)
            notes.setdefault(rule.id, []).append(_change(target, rule, base.get(rule.id)))
        parent = _parent(target, packs)
        for rule in parent.rules if parent is not None else ():
            if rule.id not in here:
                notes.setdefault(rule.id, []).append(f"`{target}`: off")
    latest = core_releases(category)[-1]
    guided = sorted(
        {note.target for pack in packs.values() for note in pack.guidance},
        key=_reading_order,
    )
    lines = [
        f"### {category}",
        "",
        f"Latest release **{latest.version}**, {latest.released}.",
    ]
    if guided:
        lines[-1] += " Writing guidance for " + ", ".join(f"`{t}`" for t in guided) + "."
    lines += [
        "",
        "| Rule | Finds | Reads | Default | Changed by provider or model |",
        "|---|---|---|---|---|",
    ]
    for rule_id in order:
        rule = found[rule_id]
        every = base[rule_id].severity if rule_id in base else "—"
        reads = " and ".join(_READS[scope] for scope in rule.scope)
        changed = "<br>".join(notes.get(rule_id, [])) or "—"
        title = rule.title.replace("|", "\\|")
        lines.append(f"| `{rule_id}` | {title} | {reads} | {every} | {changed} |")
    return "\n".join(lines)
