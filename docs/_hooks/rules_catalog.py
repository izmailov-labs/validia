"""Write the prompt rules pages from the rules validia ships.

mkdocs runs this file as a hook (``hooks:`` in ``mkdocs.yml``). ``docs/rules/`` mirrors
the core rule tree: ``default.md`` for the default files, ``<provider>/index.md`` for a
provider's, ``<provider>/<model>.md`` for one model's. Each page holds a marker, and
every build replaces it with tables read through ``validia.rules``, so the pages show
the rules in the installed package and cannot drift from them.

Each category is written once per release, as a pin to that release reads it, with a
dropdown to choose between them (``docs/javascripts/rules-releases.js``). Without
JavaScript the latest shows, and only the latest is indexed for search.

Every rule comes with its examples: the cause and effect from ``docs/rules/effects.toml``,
the text it fires on (negative) and stays quiet on (positive), and its fix. Each
category's whole-prompt examples follow its rules.

A core target without a page is a warning, so ``mkdocs build --strict`` fails until the
page is written; ``validation.nav.omitted_files`` then fails it until the page is in
the nav. A rule without a cause and effect, or an entry for no rule, is a warning too.
"""

import html
import logging
import posixpath
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Protocol

from validia.rules import (
    Example,
    Guidance,
    Release,
    Rule,
    RulePack,
    core_categories,
    core_releases,
    core_targets,
    default_rules,
    verify_core,
)

ROOT = "rules"
"""The docs folder the pages live in."""


EFFECTS = Path(__file__).resolve().parents[1] / ROOT / "effects.toml"
"""Each rule's cause and effect, which only the docs read."""


Effects = Mapping[str, tuple[str, str]]
"""Rule ids to their cause and their effect."""


_MARKER = re.compile(r"<!-- rules-(page|targets|releases)(?::\s*(\S+))? -->")


_READS = {"prompt": "prompt", "tool_description": "tool descriptions"}


_LISTED = 3  # a change carried by more files than this names a count, not the files


_log = logging.getLogger("mkdocs.hooks.rules")  # under mkdocs, so --strict counts it


class _File(Protocol):
    @property
    def src_uri(self) -> str: ...


class _Page(Protocol):
    @property
    def file(self) -> _File: ...


def on_files(files: Iterable[_File], **_: object) -> None:
    """Warn about every core target that has no page.

    Args:
        files: Every file in the docs folder.
        **_: The config mkdocs passes; unused.
    """
    present = {file.src_uri for file in files}
    catalog = _catalog(None)
    for target in catalog.targets:
        page = page_of(target)
        if page not in present:
            _log.warning("the rules have files for %s, but the docs have no %s", target, page)
    rules = catalog.rule_ids()
    for rule_id in sorted(rules - set(catalog.effects)):
        _log.warning("%s has no cause and effect for %s", EFFECTS.name, rule_id)
    for rule_id in sorted(set(catalog.effects) - rules):
        _log.warning("%s names %s, which is not a rule", EFFECTS.name, rule_id)


def on_page_markdown(markdown: str, *, page: _Page, **_: object) -> str:
    """Replace the rules markers on a page with what they stand for.

    Args:
        markdown: The page's Markdown source.
        page: The page; its path makes the links relative.
        **_: The config and files mkdocs passes; unused.

    Returns:
        The page, with every marker replaced.
    """
    return expand(markdown, page.file.src_uri)


def expand(
    markdown: str,
    src_uri: str,
    folder: Traversable | Path | None = None,
    effects: Effects | None = None,
) -> str:
    """Replace every rules marker in a page's Markdown.

    ``<!-- rules-page: <target> -->`` becomes that target's rules,
    ``<!-- rules-targets -->`` a table of every target, and ``<!-- rules-releases -->``
    every category's releases.

    Args:
        markdown: The page's Markdown source.
        src_uri: The page's path in the docs folder, as ``rules/anthropic/index.md``.
        folder: Where the rules live; the package's own when omitted.
        effects: Each rule's cause and effect; ``effects.toml`` when omitted.

    Returns:
        The page, with every marker replaced.
    """
    catalog = _catalog(folder, effects)

    def replace(match: re.Match[str]) -> str:
        kind, target = match[1], match[2]
        if kind == "targets":
            return catalog.targets_table(src_uri)
        if kind == "releases":
            return catalog.releases_page()
        if target not in catalog.targets:
            _log.warning("%s names %r, which is not a target of the rules", src_uri, target)
            return ""
        return catalog.page(target, src_uri)

    return _MARKER.sub(replace, markdown)


def page_of(target: str) -> str:
    """Name the page a target is documented on.

    Args:
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.

    Returns:
        Its path in the docs folder, as ``rules/anthropic/claude-opus-5-5.md``.
    """
    if target == "default":
        return f"{ROOT}/default.md"
    provider, _, model = target.partition("/")
    return f"{ROOT}/{provider}/{'index' if model == 'default' else model}.md"


def _link(target: str, src_uri: str) -> str:
    """A Markdown link from one page to a target's page."""
    path = posixpath.relpath(page_of(target), posixpath.dirname(src_uri))
    return f"[`{target}`]({path})"


def _reading_order(target: str) -> tuple[bool, str, bool, str]:
    """Sort targets as lint reads them: the default, then by vendor, its default first."""
    provider, _, model = target.partition("/")
    return (target != "default", provider, model != "default", model)


def _changed_at(target: str, rule: Rule) -> bool:
    """Whether the last file laid over a rule is the target's own."""
    return rule.origin[-1].startswith(f"{target} ")


def _severity(rule: Rule, *, brief: bool = False) -> str:
    """A rule's severity across the models its file reaches; brief counts long lists."""
    if not rule.models:
        return rule.severity
    if brief and len(rule.models) > _LISTED:
        return f"{rule.severity} on {len(rule.models)} models; {rule.otherwise} on the rest"
    named = ", ".join(f"`{glob}`" for glob in rule.models)
    return f"{rule.severity} on {named}; {rule.otherwise} on the rest"


def _change(rule: Rule, base: Rule | None, *, brief: bool = False) -> str:
    """What the last file laid over a rule did to it, in a few words."""
    last = rule.origin[-1].split(" ")
    kind = last[2] if len(last) > 2 else "defines"
    if base is None or kind == "replace":
        lead = "replaced" if kind == "replace" else "defined here"
        return f"{lead}, {_severity(rule, brief=brief)}"
    said = [_severity(rule, brief=brief)] if _severity(rule) != _severity(base) else []
    if rule.pattern != base.pattern:
        said.append("own pattern")
    if rule.fix != base.fix:
        said.append("own fix")
    return ", ".join(said) or "more cases"


def _row(*cells: str) -> str:
    return "| " + " | ".join(cell.replace("\n", " ") for cell in cells) + " |"


def _title(rule: Rule) -> str:
    return rule.title.replace("|", "\\|")


def _guidance(notes: Iterable[Guidance], src_uri: str) -> list[str]:
    """Writing guidance as collapsed admonitions, one per target that says it."""
    lines: list[str] = []
    for note in notes:
        lines += ["", f'??? tip "Writing for {note.target}"', "", f"    {note.summary}", ""]
        lines += [f"    - {instruction}" for instruction in note.instructions]
        if note.sources:
            lines += ["", "    Sources: " + "; ".join(note.sources) + "."]
        lines += ["", f"    From {_link(note.target, src_uri)}, {note.version}."]
    return lines


def load_effects(path: Path = EFFECTS) -> dict[str, tuple[str, str]]:
    """Read each rule's cause and effect.

    Args:
        path: The TOML file, one table per rule id with ``cause`` and ``effect``.

    Returns:
        Rule ids to their cause and their effect.
    """
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, tuple[str, str]] = {}
    for rule_id, entry in data.items():
        cause, effect = entry.get("cause"), entry.get("effect")
        if isinstance(cause, str) and isinstance(effect, str):
            out[rule_id] = (cause, effect)
        else:
            _log.warning("%s: %s needs a cause and an effect, as text", path.name, rule_id)
    return out


_MARKUP = str.maketrans({"*": "&#42;", "_": "&#95;", "`": "&#96;", "[": "&#91;", "\\": "&#92;"})


def _code(text: str) -> str:
    """Prompt text as inline code that keeps its lines, and is never read as Markdown."""
    escaped = html.escape(text, quote=False).translate(_MARKUP)
    return f"<code>{escaped.replace(chr(10), '<br>')}</code>"


def _indent(lines: Iterable[str]) -> list[str]:
    """Lines as the body of an admonition."""
    return [f"    {line}" if line else "" for line in lines]


def _rule_example(rule: Rule, flagged: str, model: str | None, effects: Effects) -> list[str]:
    """One rule's examples, collapsed: cause and effect, negative, positive, fix."""
    title = f"{rule.id}: {rule.title}".replace('"', "'")
    body: list[str] = []
    if rule.id in effects:
        cause, effect = effects[rule.id]
        body += [f"**Cause:** {cause}", "", f"**Effect:** {effect}", ""]
    body += [f"✗ **Negative:** `lint` flags it as {flagged}.", ""]
    for case in rule.fires:
        hits = [finding.excerpt for finding in rule.scan(case, model) if finding.excerpt]
        flags = " flags " + ", ".join(_code(hit) for hit in hits) if hits else ""
        body.append(f"- {_code(case)}{flags}")
    body += ["", "✓ **Positive:** `lint` stays quiet.", ""]
    body += [f"- {_code(case)}" for case in rule.quiet]
    body += ["", f"**Fix:** {rule.fix}"]
    return ["", f'??? example "{title}"', "", *_indent(body)]


def _prompt_example(example: Example, provider: str | None) -> list[str]:
    """A whole-prompt example: red when rules fire on it, green when none do."""
    kind = "failure" if example.fires else "success"
    title = f"Whole prompt: {example.name}".replace('"', "'")
    body: list[str] = []
    if example.model is not None:
        model = f"{provider}:{example.model}" if provider else example.model
        body += [f"Linted as `{model}`.", ""]
    body += ["```text", *example.text.splitlines(), "```", ""]
    if not example.fires:
        body.append("✓ `lint` flags nothing.")
    else:
        found = []
        for rule_id in sorted(example.fires):
            count = example.counts.get(rule_id)
            severity = example.severities.get(rule_id)
            found.append(
                f"`{rule_id}`"
                + (f" {count} times" if count else "")
                + (f" as {severity}" if severity else "")
            )
        body.append("✗ `lint` flags " + ", ".join(found) + ".")
    return ["", f'??? {kind} "{title}"', "", *_indent(body)]


class _Catalog:
    """The core rules, read once per build, and the Markdown each page needs."""

    def __init__(self, folder: Traversable | Path | None, effects: Effects | None = None) -> None:
        self.folder = folder
        self.effects: Effects = load_effects() if effects is None else effects
        self.categories = core_categories(folder)
        found = {target for category in self.categories for target in self._targets(category)}
        self.targets: tuple[str, ...] = tuple(sorted(found, key=_reading_order))
        self._packs: dict[tuple[str, str], dict[str, RulePack]] = {}

    def _targets(self, category: str) -> tuple[str, ...]:
        return core_targets(category, self.folder)

    def releases(self, category: str) -> tuple[Release, ...]:
        """A category's releases, newest first."""
        return tuple(reversed(core_releases(category, self.folder)))

    def packs(self, category: str, version: str) -> dict[str, RulePack]:
        """Every target of a category with files, as a pin to one release reads it."""
        key = (category, version)
        if key not in self._packs:
            # verify_core reads each target along its own chain, exactly as lint would,
            # and hands back the pack it read; its checks are the test suite's job.
            proved = verify_core(
                pins={category: version}, categories=[category], folder=self.folder
            )
            self._packs[key] = {target: pack for _, target, pack, _ in proved}
        return self._packs[key]

    def rule_ids(self) -> set[str]:
        """Every rule any release of any category has, for any target."""
        return {
            rule.id
            for category in self.categories
            for release in self.releases(category)
            for pack in self.packs(category, release.version).values()
            for rule in pack.rules
        }

    def model_pack(self, target: str, category: str, version: str) -> RulePack:
        """One model's whole chain in a category, whether or not it has files there."""
        provider, _, model = target.partition("/")
        return default_rules(
            (provider, model), pins={category: version}, categories=[category], folder=self.folder
        )

    def by_release(self, category: str, view: Callable[[Release], list[str]]) -> list[str]:
        """A category's release dropdown, then one view per release, newest first."""
        releases = self.releases(category)
        options = "".join(
            f'<option value="{release.version}"{" selected" if index == 0 else ""}>'
            f"{release.version}{' (latest)' if index == 0 else ''}</option>"
            for index, release in enumerate(releases)
        )
        lines = [
            '<div class="rules-release">',
            f'<label for="rules-release-{category}">Release</label>',
            f'<select id="rules-release-{category}" class="rules-release__select"'
            f' data-category="{category}">{options}</select>',
            "</div>",
        ]
        for index, release in enumerate(releases):
            # Older releases start hidden, and stay out of search: a hit there would
            # land on a table the reader cannot see.
            older = ' hidden="hidden" data-search-exclude=""' if index else ""
            pin = f'`{category} = "{release.version}"` under `[lint.rules]`'
            lines += [
                "",
                f'<div class="rules-release__view" data-category="{category}"'
                f' data-release="{release.version}"{older} markdown>',
                "",
                f"Released {release.released}; a pin reads it as {pin}.",
                *view(release),
                "",
                "</div>",
            ]
        return lines

    def page(self, target: str, src_uri: str) -> str:
        """Every category, as one target's page shows it."""
        provider, _, model = target.partition("/")
        if target == "default":
            intro, view = self._default_intro(src_uri), self._default_view
        elif model == "default":
            intro, view = self._provider_intro(provider, src_uri), self._provider_view
        else:
            intro, view = self._model_intro(target, src_uri), self._model_view
        lines = [intro]
        for category in self.categories:
            if model == "default" and target not in self._targets(category):
                continue  # a provider page shows only the categories it has files in
            lines += ["", f"## {category}", ""]
            lines += self.by_release(category, partial(view, target, category, src_uri))
        return "\n".join(lines)

    def _default_intro(self, src_uri: str) -> str:
        return (
            "Every rule, as its default file defines it for every model. A provider's or"
            " model's files change some of them; the last column names those, and their"
            " pages show each model's view. Without `-m`, `validia lint` reads only these."
        )

    def _default_view(self, target: str, category: str, src: str, release: Release) -> list[str]:
        packs = self.packs(category, release.version)
        base = {rule.id: rule for rule in packs["default"].rules} if "default" in packs else {}
        order, found = list(base), dict(base)
        notes: dict[str, list[str]] = {}
        for other in sorted(packs, key=_reading_order):
            if other == "default":
                continue
            pack = packs[other]
            here = {rule.id for rule in pack.rules}
            for rule in pack.rules:
                if _changed_at(other, rule):
                    if rule.id not in found:
                        found[rule.id] = rule
                        order.append(rule.id)
                    change = _change(rule, base.get(rule.id), brief=True)
                    notes.setdefault(rule.id, []).append(f"{_link(other, src)}: {change}")
            provider, _, model = other.partition("/")
            vendor = packs.get(f"{provider}/default") if model != "default" else None
            parent = vendor or packs.get("default")
            for rule in parent.rules if parent is not None else ():
                if rule.id not in here:
                    notes.setdefault(rule.id, []).append(f"{_link(other, src)}: off")
        lines = [
            "",
            _row("Rule", "Finds", "Reads", "Severity", "Changed by provider or model"),
            _row("---", "---", "---", "---", "---"),
        ]
        for rule_id in order:
            rule = found[rule_id]
            every = base[rule_id].severity if rule_id in base else "—"
            reads = " and ".join(_READS[scope] for scope in rule.scope)
            changed = "<br>".join(notes.get(rule_id, [])) or "—"
            lines.append(_row(f"`{rule_id}`", _title(rule), reads, every, changed))
        for rule_id in order:
            rule = found[rule_id]
            flagged = f"**{base[rule_id].severity}**" if rule_id in base else _severity(rule)
            lines += _rule_example(rule, flagged, None, self.effects)
        if "default" in packs:
            for example in packs["default"].examples:
                lines += _prompt_example(example, None)
        return lines

    def _provider_intro(self, provider: str, src_uri: str) -> str:
        models = [
            t for t in self.targets if t.startswith(f"{provider}/") and t != f"{provider}/default"
        ]
        text = (
            f"What the `{provider}/default` files change, laid over the"
            f" {_link('default', src_uri)} rules for every `{provider}:` model."
            " Every rule not listed here reads as it does by default."
        )
        if models:
            pages = ", ".join(_link(model, src_uri) for model in models)
            text += (
                f" These models also have files of their own: {pages}. Any other"
                f" `{provider}:` model reads the default rules and this page's changes."
            )
        return text

    def _provider_view(self, target: str, category: str, src: str, release: Release) -> list[str]:
        packs = self.packs(category, release.version)
        pack = packs.get(target)
        base_pack = packs.get("default")
        base = {rule.id: rule for rule in base_pack.rules} if base_pack is not None else {}
        lines = (
            _guidance([note for note in pack.guidance if note.target == target], src)
            if pack
            else []
        )
        changed = [rule for rule in pack.rules if _changed_at(target, rule)] if pack else []
        off = [
            rule_id
            for rule_id in base
            if pack is not None and rule_id not in {r.id for r in pack.rules}
        ]
        provider = target.partition("/")[0]
        if not changed and not off:
            examples = self._own_examples(pack, base_pack, provider)
            return [*lines, "", "No rule changes in this release.", *examples]
        lines += [
            "",
            _row("Rule", "Finds", "Default", f"On `{provider}:` models"),
            _row("---", "---", "---", "---"),
        ]
        for rule in changed:
            every = base[rule.id].severity if rule.id in base else "—"
            lines.append(
                _row(f"`{rule.id}`", _title(rule), every, _change(rule, base.get(rule.id)))
            )
        lines += [
            _row(f"`{rule_id}`", _title(base[rule_id]), base[rule_id].severity, "off")
            for rule_id in off
        ]
        for rule in changed:
            lines += _rule_example(rule, _severity(rule), None, self.effects)
        lines += self._own_examples(pack, base_pack, provider)
        return lines

    @staticmethod
    def _own_examples(pack: RulePack | None, under: RulePack | None, provider: str) -> list[str]:
        """The whole-prompt examples a pack has that the pack beneath it does not."""
        if pack is None:
            return []
        seen = {(e.name, e.text) for e in under.examples} if under is not None else set()
        return [
            line
            for example in pack.examples
            if (example.name, example.text) not in seen
            for line in _prompt_example(example, provider)
        ]

    def _model_intro(self, target: str, src_uri: str) -> str:
        provider, _, model = target.partition("/")
        chain = " → ".join(
            _link(t, src_uri)
            for t in ("default", f"{provider}/default", target)
            if t in self.targets
        )
        return (
            f"Every rule as `validia lint -m {provider}:{model}` reads it, along {chain}."
            " **On this model** is the severity `lint` gives it, in bold beside the default"
            " where the two differ."
        )

    def _model_view(self, target: str, category: str, src: str, release: Release) -> list[str]:
        model = target.partition("/")[2]
        pack = self.model_pack(target, category, release.version)
        default = self.packs(category, release.version).get("default")
        base = {rule.id: rule for rule in default.rules} if default is not None else {}
        lines = _guidance(pack.guidance, src)
        lines += [
            "",
            _row("Rule", "Finds", "On this model", "From"),
            _row("---", "---", "---", "---"),
        ]
        changed: list[Rule] = []
        for rule in pack.rules:
            here = rule.severity_for(model)
            every = base[rule.id].severity if rule.id in base else "—"
            shown = f"**{here}** (default {every})" if here != every else here
            layers = [
                entry.split(" ")[0] for entry in rule.origin if not entry.startswith("default ")
            ]
            files = "<br>".join(_link(layer, src) for layer in dict.fromkeys(layers)) or "—"
            lines.append(_row(f"`{rule.id}`", _title(rule), shown, files))
            if layers:
                changed.append(rule)
        off = [rule_id for rule_id in base if rule_id not in {rule.id for rule in pack.rules}]
        if off:
            lines += [
                "",
                "Off on this model: " + ", ".join(f"`{rule_id}`" for rule_id in off) + ".",
            ]
        for rule in changed:
            here = rule.severity_for(model)
            lines += _rule_example(rule, f"**{here}** on this model", model, self.effects)
        provider = target.partition("/")[0]
        lines += [
            line
            for example in pack.examples
            if example.model == model
            for line in _prompt_example(example, provider)
        ]
        lines += [
            "",
            f"Examples for every other rule are on the {_link('default', src)} page.",
        ]
        return lines

    def targets_table(self, src_uri: str) -> str:
        """Every target the core has files for, and what each one's files do."""
        changes: dict[str, list[str]] = {target: [] for target in self.targets}
        guidance: dict[str, list[str]] = {target: [] for target in self.targets}
        total = 0
        for category in self.categories:
            packs = self.packs(category, self.releases(category)[0].version)
            total += len(packs["default"].rules) if "default" in packs else 0
            for target, pack in packs.items():
                if target != "default":
                    changes[target] += [rule.id for rule in pack.rules if _changed_at(target, rule)]
                if any(note.target == target for note in pack.guidance):
                    guidance[target].append(category)
        lines = [
            _row("Target", "Read for", "Rules it changes", "Guidance for"),
            _row("---", "---", "---", "---"),
        ]
        for target in self.targets:
            provider, _, model = target.partition("/")
            if target == "default":
                reach, changed = "every model", f"defines all {total}"
            else:
                reach = (
                    f"every `{provider}:` model" if model == "default" else f"`{provider}:{model}`"
                )
                changed = ", ".join(f"`{rule}`" for rule in changes[target]) or "—"
            notes = ", ".join(guidance[target]) or "—"
            lines.append(_row(_link(target, src_uri), reach, changed, notes))
        return "\n".join(lines)

    def releases_page(self) -> str:
        """Every category's releases, newest first, with what changed in each."""
        lines: list[str] = []
        for category in self.categories:
            lines += ["", f"### {category}"]
            for index, release in enumerate(self.releases(category)):
                latest = " (latest)" if index == 0 else ""
                lines += ["", f"**{release.version}**{latest}, released {release.released}:", ""]
                for change, files in release.changes:
                    where = (
                        f"{len(files)} files"
                        if len(files) > _LISTED
                        else ", ".join(f"`{category}/{file}`" for file in files)
                    )
                    lines.append(f"- {change} ({where})")
        return "\n".join(lines).lstrip("\n")


_CORE: _Catalog | None = None
"""The package's own rules, read once per build: mkdocs runs this file anew for each."""


def _catalog(folder: Traversable | Path | None, effects: Effects | None = None) -> _Catalog:
    """The catalog for a rules folder; the package's own is read only once."""
    global _CORE
    if folder is not None or effects is not None:
        return _Catalog(folder, effects)
    if _CORE is None:
        _CORE = _Catalog(None)
    return _CORE
