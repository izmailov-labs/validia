"""The core rule folder: its categories, rules, targets, releases, and the pins that pick one."""

from collections.abc import Sequence
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from .model import _PIN, _RESERVED, _SEMVER, CATEGORIES, Release, RuleError
from .reading import _header, _load_toml, _Reader


def _root(folder: Traversable | Path | None) -> Traversable | Path:
    """Where core rules live: the package's own, unless a test gives another."""
    if folder is None:
        return resources.files(__package__).joinpath("core")
    return folder


def _dirs(node: Traversable | Path) -> list[str]:
    if not node.is_dir():
        return []
    return sorted(
        entry.name
        for entry in node.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    )


def _semver(text: str) -> tuple[int, int, int] | None:
    match = _SEMVER.fullmatch(text)
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def _versions_in(node: Traversable | Path) -> list[str]:
    """The releases a target folder has a file for, oldest first."""
    if not node.is_dir():
        return []
    stems = [
        entry.name.removesuffix(".toml")
        for entry in node.iterdir()
        if entry.name.endswith(".toml") and not entry.name.endswith(".cases.toml")
    ]
    ordered = sorted((parts, stem) for stem in stems if (parts := _semver(stem)) is not None)
    return [stem for _, stem in ordered]


def _targets_in(node: Traversable | Path) -> list[str]:
    """The targets a rule, examples or guidance folder has: default, then by vendor."""
    names = _dirs(node)
    out = ["default"] if "default" in names else []
    for provider in (name for name in names if name != "default"):
        inside = _dirs(node.joinpath(provider))
        if "default" in inside:
            out.append(f"{provider}/default")
        out += [f"{provider}/{model}" for model in inside if model != "default"]
    return out


def _at(node: Traversable | Path, target: str) -> Traversable | Path:
    return node.joinpath(*target.split("/"))


def core_categories(folder: Traversable | Path | None = None) -> tuple[str, ...]:
    """List the core categories, in reading order, then any others by name.

    Args:
        folder: Where the rules live; the package's own when omitted.

    Returns:
        The category names.
    """
    found = set(_dirs(_root(folder)))
    known = [category for category in CATEGORIES if category in found]
    return (*known, *sorted(found - set(known)))


def core_rules(category: str, folder: Traversable | Path | None = None) -> tuple[str, ...]:
    """List a category's rule folders, by name.

    Args:
        category: The category.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        Rule names, such as ``show-reasoning``; the rule's id is ``reasoning/show-reasoning``.
    """
    return tuple(name for name in _dirs(_root(folder).joinpath(category)) if name not in _RESERVED)


def _subjects(category: str, folder: Traversable | Path | None) -> list[str]:
    """A category's folders: each rule's, then examples and guidance."""
    base = _root(folder).joinpath(category)
    reserved = [name for name in _RESERVED if base.joinpath(name).is_dir()]
    return [*core_rules(category, folder), *reserved]


def core_targets(category: str, folder: Traversable | Path | None = None) -> tuple[str, ...]:
    """List every target any file of a category is for: the default, then by vendor.

    Args:
        category: The category.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        Targets such as ``anthropic/claude-opus-5-5``.
    """
    base = _root(folder).joinpath(category)
    found = {
        target
        for name in _subjects(category, folder)
        for target in _targets_in(base.joinpath(name))
    }
    return _ordered(found)


def _ordered(found: set[str]) -> tuple[str, ...]:
    """Targets in reading order: the default, then by vendor, its default first."""
    out = ["default"] if "default" in found else []
    for provider in sorted({target.split("/")[0] for target in found if target != "default"}):
        if f"{provider}/default" in found:
            out.append(f"{provider}/default")
        out += sorted(
            t for t in found if t.startswith(f"{provider}/") and t != f"{provider}/default"
        )
    return tuple(out)


def core_versions(
    category: str,
    name: str,
    target: str = "default",
    folder: Traversable | Path | None = None,
) -> tuple[str, ...]:
    """List the releases one rule, or a category's examples or guidance, changed in.

    Args:
        category: The category.
        name: The rule's folder name, or ``examples`` or ``guidance``.
        target: The target folder.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        Versions, oldest first; empty when there is no such folder.
    """
    return tuple(_versions_in(_at(_root(folder).joinpath(category, name), target)))


def _known_releases(category: str, folder: Traversable | Path | None) -> list[str]:
    """Every release of a category: each version any of its files is named for."""
    base = _root(folder).joinpath(category)
    found = {
        version
        for name in _subjects(category, folder)
        for target in _targets_in(base.joinpath(name))
        for version in _versions_in(_at(base.joinpath(name), target))
    }
    return sorted(found, key=lambda version: _semver(version) or (0, 0, 0))


def core_releases(category: str, folder: Traversable | Path | None = None) -> tuple[Release, ...]:
    """List a category's releases, oldest first, with what changed in each.

    Args:
        category: The category.
        folder: Where the rules live; the package's own when omitted.

    Returns:
        The releases.

    Raises:
        RuleError: If a file's header is malformed, or a release's files disagree on
            the day it was released.
    """
    base = _root(folder).joinpath(category)
    found: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}
    for name in _subjects(category, folder):
        for target in _targets_in(base.joinpath(name)):
            place = _at(base.joinpath(name), target)
            for version in _versions_in(place):
                source = f"{category}/{name}/{target}/{version}.toml"
                reader = _Reader()
                released, changes = _header(
                    reader, _load_toml(place.joinpath(f"{version}.toml"), source)
                )
                if reader.problems:
                    raise RuleError(source, reader.problems)
                found.setdefault(version, []).append((f"{name} {target}", released, changes))
    releases: list[Release] = []
    for version in _known_releases(category, folder):
        files = found[version]
        days = sorted({released for _, released, _ in files})
        if len(days) > 1:
            raise RuleError(
                f"{category} {version}", [f"its files disagree on the day: {', '.join(days)}"]
            )
        grouped: dict[str, list[str]] = {}
        for where, _, changes in files:
            for change in changes:
                grouped.setdefault(change, []).append(where)
        changed = tuple((change, tuple(wheres)) for change, wheres in grouped.items())
        releases.append(Release(category, version, days[0], changed))
    return tuple(releases)


def is_pin(value: str) -> bool:
    """Say whether a value can pin a category: ``latest``, ``1``, ``1.2`` or ``1.2.3``.

    Args:
        value: The pin as written.

    Returns:
        Whether it is one.
    """
    return _PIN.fullmatch(value) is not None


def _allows(pin: str, version: str) -> bool:
    """Say whether a file named for a release is part of what a pin reads.

    A pin is a ceiling: ``"1"`` reads up to the last 1.x.y, ``"1.2"`` up to the last
    1.2.y, ``"1.2.3"`` up to exactly that. A rule untouched since 1.0.0 is read under
    every one of them; a rule first released at 2.0.0 under none.
    """
    parts = _semver(version)
    if parts is None:
        return False
    if pin == "latest":
        return True
    wanted = tuple(int(part) for part in pin.split("."))
    return parts[: len(wanted)] <= wanted


def _release_of(category: str, pin: str, folder: Traversable | Path | None) -> str:
    """Name the category release a pin reads: the newest one it allows.

    Raises:
        RuleError: If no release matches the pin, so a typo is not read as a ceiling.
    """
    known = _known_releases(category, folder)
    if pin == "latest":
        chosen = known[-1:]
    else:
        wanted = tuple(int(part) for part in pin.split("."))
        exists = any((_semver(v) or ())[: len(wanted)] == wanted for v in known)
        chosen = [version for version in known if _allows(pin, version)] if exists else []
    if not chosen:
        listed = ", ".join(known) or "none"
        raise RuleError(
            "the core rules", [f"{category} has no release matching {pin!r}; it has {listed}"]
        )
    return chosen[-1]


def _pick(versions: Sequence[str], release: str) -> str | None:
    """The newest file a release reads, if any."""
    allowed = [version for version in versions if _allows(release, version)]
    return allowed[-1] if allowed else None
