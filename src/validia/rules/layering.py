"""Laying rule files over each other along a model's chain, for the core and a project."""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from .catalog import (
    _at,
    _ordered,
    _pick,
    _release_of,
    _root,
    _semver,
    _targets_in,
    _versions_in,
    core_categories,
    core_rules,
    core_targets,
    is_pin,
)
from .matching import verify_rules
from .model import (
    _GUIDANCE_FIELDS,
    _HEADER,
    _NAME_PROBLEM,
    _OPERATIONS,
    _PROJECT_RANK,
    _RESERVED,
    _RULE_NAME,
    Example,
    Guidance,
    Part,
    Rule,
    RuleCheck,
    RuleError,
    RulePack,
    Target,
)
from .reading import _build, _example, _extend, _header, _load_toml, _Reader, _tables


@dataclass
class _Category:
    """One category as it is read: the rules so far, and what each file did to them."""

    name: str
    release: str
    rules: dict[str, Rule] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    retired: dict[str, str] = field(default_factory=dict)
    history: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    examples: list[tuple[int, Example]] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    guidance: list[Guidance] = field(default_factory=list)

    def put(self, rule: Rule, rank: int, change: str = "") -> None:
        self.rules[rule.id] = rule
        self.seen.add(rule.id)
        self.removed.discard(rule.id)
        if change:
            self.history.setdefault(rule.id, []).append((rank, change))

    def drop(self, rule_id: str, rank: int) -> None:
        self.rules.pop(rule_id, None)
        self.seen.add(rule_id)  # still a rule an older example may name
        self.removed.add(rule_id)
        self.history.setdefault(rule_id, []).append((rank, "reset"))


_Fetch = Callable[[_Reader, str, object], Rule | None]
"""Reads one rule as an older release had it, for a project's from."""


def _older(version: str, than: str) -> bool:
    """Whether one release came before another."""
    return (_semver(version) or (0, 0, 0)) < (_semver(than) or (0, 0, 0))


def _only(reader: _Reader, body: Mapping[str, Any], mode: str) -> None:
    """Refuse fields beside an operation that takes nothing else."""
    extra = [key for key in body if key != mode]
    if extra:
        reader.problems.append(f"a `{mode}` rule takes only {mode}, not {', '.join(extra)}")


def _operate(
    reader: _Reader,
    body: Mapping[str, Any],
    cases: Mapping[str, Any] | None,
    rule_id: str,
    state: _Category,
    rank: int,
    origin: str,
    *,
    default: bool = False,
    core: bool = True,
    fetch: _Fetch | None = None,
    version: str | None = None,
) -> None:
    """Lay one file over the rule beneath it: define, extend, replace or turn it off.

    Args:
        reader: Collects problems.
        body: The file's fields, without its header.
        cases: Its cases file, if it has one.
        rule_id: The rule, named by its folder.
        state: The category so far.
        rank: Where the file sits: default 0, vendor 1, model 2, project 3.
        origin: The file, as the rule's history records it.
        default: Whether it is a default file, which defines and never extends.
        core: Whether it ships with validia, so a new rule needs a title.
        fetch: For a project file, reads a rule as an older release had it.
        version: The release a core file is named for; ``None`` for a project's.
    """
    retired = state.retired.get(rule_id)
    if retired is not None and version is not None and _older(version, retired):
        # Written before the default retired the rule: there is nothing left for it to change.
        return
    known = state.rules.get(rule_id)
    modes = [mode for mode in _OPERATIONS if mode in body]
    if len(modes) > 1:
        reader.problems.append(f"choose one of {', '.join(modes)}")
        return
    mode = modes[0] if modes else ""
    if mode in ("enabled", "from") and cases is not None:
        reader.problems.append(f"cases: a `{mode}` rule has no cases of its own")
    if mode == "enabled":
        _only(reader, body, "enabled")
        if reader.flag(body, "enabled", "", default=True):
            reader.problems.append("enabled: only false means something; it turns the rule off")
        elif default:
            # In a default folder, a file that turns the rule off retires it: the older
            # file that defined it is not read once a newer one is out.
            state.drop(rule_id, rank)
            if version is not None:
                state.retired[rule_id] = version
        elif known is None and rule_id not in state.removed:
            reader.problems.append(f"enabled: no rule {rule_id} to turn off here")
        else:
            state.drop(rule_id, rank)
        return
    if mode == "from":
        if fetch is None:
            reader.problems.append("from: only a project's rules can fall back to an older release")
            return
        _only(reader, body, "from")
        older = fetch(reader, rule_id, body["from"])
        if older is not None:
            entry = f"{origin} from {body['from']}"
            state.put(replace(older, origin=(*older.origin, entry)), rank, "reset")
        return
    if mode == "extend":
        if default:
            reader.problems.append(
                "extend: a default file defines its rule; it has nothing to extend"
            )
        elif body["extend"] is not True:
            reader.problems.append("extend: give true, or leave it out to replace the rule")
        elif known is None:
            if retired is not None:
                why = (
                    f"{state.name} {retired} retired it; remove this file, or pin"
                    f" lint.rules.{state.name} to a release before {retired}"
                )
            elif rule_id in state.removed:
                why = "it was turned off below this file"
            else:
                why = "it is not defined below this file"
            reader.problems.append(f"extend: nothing to extend: {why}")
        else:
            rule = _extend(reader, body, cases or {}, known)
            state.put(replace(rule, origin=(*known.origin, f"{origin} extend")), rank, "extend")
        return
    rule = _build(reader, body, cases or {}, rule_id, state.name, titled=core, everyone=default)
    if known is None:
        state.put(replace(rule, origin=(origin,)), rank)
    else:
        state.put(replace(rule, origin=(*known.origin, f"{origin} replace")), rank, "reset")


@dataclass(frozen=True, slots=True)
class _Tree:
    """Where rule files are read from: the core, or a project's ``rules/`` folder.

    Attributes:
        root: The folder that holds the categories.
        label: How its files are named in problems and in a rule's history.
        rank: Where its default files sit in a chain; its vendor's and model's follow.
        core: Whether it ships with validia, and so is held to a release's rules.
        fetch: For a project, reads a core rule as an older release had it.
    """

    root: Traversable | Path
    label: str = ""
    rank: int = 0
    core: bool = True
    fetch: _Fetch | None = None


def _read_rule(
    state: _Category, name: str, chain: Sequence[str], tree: _Tree, release: str
) -> None:
    """Read one rule's folder along a chain of targets, each file over the one before."""
    node = tree.root.joinpath(state.name, name)
    where = f"{tree.label}{state.name}/{name}"
    rule_id = f"{state.name}/{name}"
    if not _RULE_NAME.fullmatch(name):
        raise RuleError(where, [_NAME_PROBLEM])
    targets = _targets_in(node)
    if not targets:
        raise RuleError(where, ["no target folders: give it default/, or a vendor's or model's"])
    for step, target in enumerate(chain):
        if target not in targets:
            continue
        place = _at(node, target)
        version = _pick(_versions_in(place), release)
        if version is None:
            continue
        source = f"{where}/{target}/{version}.toml"
        data = _load_toml(place.joinpath(f"{version}.toml"), source)
        extra = place.joinpath(f"{version}.cases.toml")
        cases = (
            _load_toml(extra, f"{where}/{target}/{version}.cases.toml") if extra.is_file() else None
        )
        reader = _Reader()
        released, changes = _header(reader, data, required=tree.core)
        body = {key: value for key, value in data.items() if key not in _HEADER}
        _operate(
            reader,
            body,
            cases,
            rule_id,
            state,
            tree.rank + step,
            f"{tree.label}{target} {version}",
            default=tree.core and target == "default",
            core=tree.core,
            fetch=tree.fetch,
            version=version if tree.core else None,
        )
        if reader.problems:
            raise RuleError(source, reader.problems)
        if tree.core:
            state.parts.append(Part(state.name, name, target, version, released, changes))


def _own_model(target: str) -> str | None:
    """The model a target folder is for, if it is one model's."""
    provider, _, model = target.partition("/")
    return model if provider != "default" and model != "default" else None


def _read_extras(state: _Category, chain: Sequence[str], tree: _Tree, release: str) -> None:
    """Read a category's examples and guidance along a chain of targets."""
    base = tree.root.joinpath(state.name)
    for name in _RESERVED:
        node = base.joinpath(name)
        targets = _targets_in(node)
        for step, target in enumerate(chain):
            if target not in targets:
                continue
            rank = tree.rank + step
            place = _at(node, target)
            version = _pick(_versions_in(place), release)
            if version is None:
                continue
            source = f"{tree.label}{state.name}/{name}/{target}/{version}.toml"
            data = _load_toml(place.joinpath(f"{version}.toml"), source)
            reader = _Reader()
            released, changes = _header(reader, data, required=tree.core)
            if name == "examples":
                reader.fields(data, "", (*_HEADER, "examples"))
                for index, table in enumerate(_tables(reader, data, "examples")):
                    example = _example(
                        reader,
                        table,
                        f"examples[{index}].",
                        state.seen,
                        state.name,
                        _own_model(target),
                    )
                    state.examples.append((rank, example))
            else:
                reader.fields(data, "", (*_HEADER, *_GUIDANCE_FIELDS))
                instructions = reader.texts(data, "instructions", "")
                if not instructions:
                    reader.problems.append("instructions: give at least one")
                state.guidance.append(
                    Guidance(
                        state.name,
                        target,
                        version,
                        reader.text(data, "summary", "", required=True),
                        instructions,
                        reader.texts(data, "sources", ""),
                    )
                )
            if reader.problems:
                raise RuleError(source, reader.problems)
            if tree.core:
                state.parts.append(Part(state.name, name, target, version, released, changes))


def _chain(target: Target | None) -> list[str]:
    """The targets a model reads, most general first."""
    if target is None:
        return ["default"]
    provider, model = target
    return list(dict.fromkeys(["default", f"{provider}/default", f"{provider}/{model}"]))


def _read_category(
    category: str, chain: Sequence[str], pin: str, folder: Traversable | Path | None
) -> _Category:
    state = _Category(category, _release_of(category, pin, folder))
    tree = _Tree(_root(folder))
    for name in core_rules(category, folder):
        _read_rule(state, name, chain, tree, state.release)
    _read_extras(state, chain, tree, state.release)
    return state


def _keeps(
    rank: int,
    example: Example,
    history: Mapping[str, Sequence[tuple[int, str]]],
    removed: Collection[str] = (),
) -> bool:
    """Say whether an example still holds over the files laid after its own.

    One that names a rule a later file replaced, turned off or took from an older
    release goes; so does one that asserts the severity of a rule a later file changed,
    and one that names a rule that is gone -- retired since the example was written --
    because a rule that is not there can no longer fire. So does one whose category has
    a rule a later file replaced or brought back, named or not: that rule may now fire on
    text the example says it leaves alone.
    """
    named = example.fires | set(example.counts)
    if named & set(removed):
        return False
    for rule, changes in history.items():
        if rule in removed or not any(later > rank and c == "reset" for later, c in changes):
            continue
        return False
    for rule in example.severities:
        if any(later > rank for later, _ in history.get(rule, ())):
            return False
    return True


def _pack(states: Sequence[_Category], target: Target | None, project: str = "") -> RulePack:
    return RulePack(
        rules=tuple(rule for state in states for rule in state.rules.values()),
        examples=tuple(
            example
            for state in states
            for rank, example in state.examples
            if _keeps(rank, example, state.history, state.removed)
        ),
        parts=tuple(part for state in states for part in state.parts),
        guidance=tuple(note for state in states for note in state.guidance),
        releases=tuple((state.name, state.release) for state in states),
        target=target,
        project=project,
    )


def _narrow(pack: RulePack, categories: Sequence[str] | None) -> RulePack:
    if categories is None:
        return pack
    unknown = [category for category in categories if category not in pack.categories]
    if unknown:
        raise RuleError(
            "the rules",
            [f"no category {name!r}; there are {', '.join(pack.categories)}" for name in unknown],
        )
    return pack.only(categories)


def _core(
    target: Target | None, pins: Mapping[str, str] | None, folder: Traversable | Path | None
) -> list[_Category]:
    chosen = pins or {}
    chain = _chain(target)
    return [
        _read_category(category, chain, chosen.get(category, "latest"), folder)
        for category in core_categories(folder)
    ]


def default_rules(
    target: Target | None = None,
    *,
    pins: Mapping[str, str] | None = None,
    categories: Sequence[str] | None = None,
    folder: Traversable | Path | None = None,
) -> RulePack:
    """Load the core rules for a target: every rule, read along its chain.

    Args:
        target: The model, as ``(provider, model)``; ``None`` reads only the default
            files, for every model alike.
        pins: Category names to the release to read; ``latest`` for the rest.
        categories: The categories to keep; every one when omitted.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        The combined pack.

    Raises:
        RuleError: If a pin matches no release, or a file is malformed.
    """
    return _narrow(_pack(_core(target, pins, folder), target), categories)


def load_rules(
    path: Path,
    *,
    target: Target | None = None,
    pins: Mapping[str, str] | None = None,
    categories: Sequence[str] | None = None,
    folder: Traversable | Path | None = None,
) -> RulePack:
    """Load a project's ``rules/`` folder, laid over the core rules for a target.

    The folder has the core's layout and is read by the same code, after the core:
    each rule's chain runs through the core's default, vendor and model files, then the
    project's. Every file in the folder is checked, whichever categories are kept, and
    every problem is reported at once.

    Args:
        path: The project's ``rules/`` folder.
        target: The model, as ``(provider, model)``.
        pins: Category names to the core release to read; ``latest`` for the rest.
        categories: The categories to keep, the project's own included.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        The combined pack.

    Raises:
        RuleError: If either side is malformed, listing every problem in the folder.
        OSError: If a file cannot be read.
    """
    states = {state.name: state for state in _core(target, pins, folder)}
    owner = {
        f"{category}/{name}": category
        for category in core_categories(folder)
        for name in core_rules(category, folder)
    }
    chain = _chain(target)
    core = _Tree(_root(folder))

    def fetch(reader: _Reader, rule_id: str, raw: object) -> Rule | None:
        category = owner.get(rule_id)
        pin = str(raw)
        if category is None:
            reader.problems.append(
                f"from: {rule_id} is not a core rule, so it has no older release"
            )
            return None
        if not is_pin(pin) or pin == "latest":
            reader.problems.append('from: expected a release, as in "1" or "1.0.0"')
            return None
        try:
            older = _Category(category, _release_of(category, pin, folder))
        except RuleError as exc:
            reader.problems.append(f"from: {exc.problems[0]}")
            return None
        _read_rule(older, rule_id.split("/", 1)[1], chain, core, older.release)
        found = older.rules.get(rule_id)
        if found is None:
            reader.problems.append(f"from: {category} {older.release} has no rule {rule_id}")
        return found

    if not path.is_dir():
        raise RuleError(
            path.name, [f"expected a folder: {path.name}/<category>/<rule>/default/1.0.0.toml"]
        )
    tree = _Tree(path, f"{path.name}/", _PROJECT_RANK, core=False, fetch=fetch)
    problems: list[str] = []
    for entry in sorted(path.iterdir()):
        shown = f"{path.name}/{entry.name}"
        if entry.name.startswith((".", "_")):
            continue
        if not entry.is_dir():
            if entry.suffix == ".toml":
                problems.append(
                    f"{shown}: rules go in {path.name}/<category>/<rule>/default/1.0.0.toml"
                )
            continue
        state = states.setdefault(entry.name, _Category(entry.name, ""))
        for item in sorted(entry.iterdir()):
            where = f"{shown}/{item.name}"
            if item.name.startswith((".", "_")):
                continue
            if not item.is_dir():
                if item.suffix == ".toml":
                    problems.append(f"{where}: a rule's files go in {shown}/<rule>/default/")
                continue
            problems += _strays(item, where)
            if item.name in _RESERVED:
                continue
            try:
                _read_rule(state, item.name, chain, tree, "latest")
            except RuleError as exc:
                problems += [f"{exc.source}: {problem}" for problem in exc.problems]
                rule_id = f"{entry.name}/{item.name}"
                elsewhere = [other for other in owner if other.split("/", 1)[1] == item.name]
                if rule_id not in owner and elsewhere:
                    problems.append(
                        f"{where}: did you mean {elsewhere[0]}? Its folder is"
                        f" {path.name}/{elsewhere[0]}/"
                    )
        try:
            _read_extras(state, chain, tree, "latest")
        except RuleError as exc:
            problems += [f"{exc.source}: {problem}" for problem in exc.problems]
    if problems:
        raise RuleError(f"{path.name}/", problems)
    known = core_categories(folder)
    ordered = [states[name] for name in known] + [
        states[name] for name in sorted(states) if name not in known
    ]
    return _narrow(_pack(ordered, target, path.name), categories)


def _strays(node: Path, where: str) -> list[str]:
    """Name every file in a rule, examples or guidance folder that would never be read."""
    problems = [
        f"{where}/{entry.name}: a file here is never read; put it in {where}/default/"
        for entry in sorted(node.iterdir())
        if entry.is_file() and entry.suffix == ".toml"
    ]
    for provider in sorted(entry for entry in node.iterdir() if entry.is_dir()):
        if provider.name == "default":
            continue
        problems += [
            f"{where}/{provider.name}/{entry.name}: a file here is never read; put it in"
            f" {where}/{provider.name}/default/ or {where}/{provider.name}/<model>/"
            for entry in sorted(provider.iterdir())
            if entry.is_file() and entry.suffix == ".toml"
        ]
    for target in _targets_in(node):
        place = node.joinpath(*target.split("/"))
        for entry in sorted(place.iterdir()):
            if not entry.is_file() or entry.suffix != ".toml":
                continue
            stem = entry.name.removesuffix(".toml")
            version = stem.removesuffix(".cases")
            if _semver(version) is None:
                problems.append(
                    f"{where}/{target}/{entry.name}: name it for its version, as in 1.0.0.toml"
                )
            elif stem.endswith(".cases") and not (place / f"{version}.toml").is_file():
                problems.append(
                    f"{where}/{target}/{entry.name}: cases without their rule, {version}.toml"
                )
    return problems


def verify_project(
    path: Path,
    *,
    pins: Mapping[str, str] | None = None,
    folder: Traversable | Path | None = None,
) -> list[tuple[str, RulePack, list[RuleCheck]]]:
    """Prove a project's ``rules/`` folder for every target it has files for.

    The default, then each vendor's and each model's, each read over the core, so a
    project's model-specific cases and examples are proved on their own model.

    Args:
        path: The project's ``rules/`` folder.
        pins: Category names to the core release to read; ``latest`` for the rest.
        folder: Where the core rules live; the package's own when omitted.

    Returns:
        ``(target, pack, checks)`` for every target proved.

    Raises:
        RuleError: If the folder or the core is malformed.
    """
    found = {"default"}
    for category in (entry for entry in sorted(path.iterdir()) if entry.is_dir()):
        for item in (entry for entry in sorted(category.iterdir()) if entry.is_dir()):
            found |= set(_targets_in(item))
    out: list[tuple[str, RulePack, list[RuleCheck]]] = []
    for name in _ordered(found):
        provider, _, model = name.partition("/")
        target: Target | None = None if name == "default" else (provider, model)
        pack = load_rules(path, target=target, pins=pins, folder=folder)
        out.append((name, pack, verify_rules(pack)))
    return out


def verify_core(
    *,
    pins: Mapping[str, str] | None = None,
    categories: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
    folder: Traversable | Path | None = None,
) -> list[tuple[str, str, RulePack, list[RuleCheck]]]:
    """Prove every core target in its own chain: each as it would be linted.

    A vendor's files are proved over the default; a model's over both. Each examples
    file runs as its own model sees the rules, so a model's severities are tested where
    they are written.

    Args:
        pins: Category names to the release to read; ``latest`` for the rest.
        categories: The categories to prove; every core category when omitted.
        targets: The targets to prove, as ``anthropic/claude-opus-5-5``; all when omitted.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        ``(category, target, pack, checks)`` for every target proved.
    """
    chosen = pins or {}
    out: list[tuple[str, str, RulePack, list[RuleCheck]]] = []
    for category in core_categories(folder) if categories is None else categories:
        for name in core_targets(category, folder):
            if targets is not None and name not in targets:
                continue
            provider, _, model = name.partition("/")
            if name == "default" or model == "default":
                target: Target | None = None
                chain = list(dict.fromkeys(["default", name]))
            else:
                target = (provider, model)
                chain = _chain(target)
            state = _read_category(category, chain, chosen.get(category, "latest"), folder)
            pack = _pack([state], target)
            out.append((category, name, pack, verify_rules(pack)))
    return out
