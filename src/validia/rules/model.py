"""What a rule is, and what checking one produces: rules, findings, packs, releases."""

import fnmatch
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeVar

Severity = Literal["info", "warn", "error"]


Scope = Literal["prompt", "tool_description"]


Target = tuple[str, str]


"""A model to resolve rules for: its provider slug and its name, as franca names them."""


SEVERITIES: tuple[Severity, ...] = ("info", "warn", "error")


"""Severities, least to most."""


CATEGORIES = ("wording", "context", "reasoning", "output", "tools", "security", "maintenance")


"""The core categories, in reading order."""


PROJECT_RULES = "rules"


"""The project folder laid over the core rules."""


_SCOPES: tuple[Scope, ...] = ("prompt", "tool_description")


_UNITS: tuple[Literal["sentence", "line"], ...] = ("sentence", "line")


_CONFIDENCES: tuple[Literal["high", "med", "low"], ...] = ("high", "med", "low")


_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


_PIN = re.compile(r"latest|\d+(\.\d+){0,2}")


_RULE_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


_RESERVED = ("examples", "guidance")


_NAME_PROBLEM = "not a rule name: name a rule's folder in lowercase words, as in show-reasoning"


_T = TypeVar("_T", bound=str)


_RULE_FIELDS = (
    "title",
    "pattern",
    "fix",
    "severity",
    "scope",
    "case_sensitive",
    "unless",
    "run",
    "unit",
    "at_least",
    "missing",
    "models",
    "otherwise",
    "confidence",
    "judge",
)


_OPERATIONS = ("enabled", "from", "extend")


_HEADER = ("released", "changes")


_GUIDANCE_FIELDS = ("summary", "instructions", "sources")


_EXAMPLE_FIELDS = ("name", "text", "scope", "fires", "counts", "model")


_SENTENCE = re.compile(r"(?<=[.!?])[ \t]+|\n")


_PROJECT_RANK = 3  # the project's default; the core's are default 0, vendor 1, model 2


class RuleError(ValueError):
    """Rules are malformed; ``problems`` lists every reason.

    Attributes:
        source: The file or folder the problems are in.
        problems: One line per problem.
    """

    def __init__(self, source: str, problems: Sequence[str]) -> None:
        """Keep the problems, and say them all in the message."""
        self.source = source
        self.problems = list(problems)
        count = len(self.problems)
        lines = "".join(f"\n  {problem}" for problem in self.problems)
        super().__init__(f"{source} has {count} problem{'s' if count != 1 else ''}:{lines}")


@dataclass(frozen=True, slots=True)
class Rule:
    """One check on prompt text.

    Attributes:
        id: Names the rule: its category and its folder, as in ``wording/capitals``.
        title: What it finds, in a few words; the rule reference's heading.
        pattern: What to look for. With ``missing``, what should be there.
        fix: What to do about a hit.
        severity: How much a hit matters, on the models in ``models``.
        scope: The text it reads: the prompt, or tool descriptions.
        case_sensitive: Whether case matters, as it does for capitals.
        unless: A hit is dropped when this matches in its sentence or the next.
        run: Fire once on this many consecutive matching sentences or lines.
        unit: What ``run`` counts: ``sentence`` or ``line``.
        at_least: Fire once when the pattern matches this many times.
        missing: Fire when the pattern matches nowhere.
        models: Model names (globs) the severity applies to; empty means all.
        otherwise: The severity on every other model.
        confidence: How sure a hit is: ``high``, ``med`` or ``low``.
        judge: The class set a judge would sort hits into, when one is needed.
        category: The category the rule belongs to.
        fires: Text the rule must fire on.
        quiet: Text the rule must stay quiet on.
        origin: Every file laid over it, in order, as anthropic/default 1.0.0 extend.
    """

    id: str
    pattern: str
    fix: str
    title: str = ""
    severity: Severity = "warn"
    scope: tuple[Scope, ...] = ("prompt",)
    case_sensitive: bool = False
    unless: str = ""
    run: int = 0
    unit: Literal["sentence", "line"] = "sentence"
    at_least: int = 0
    missing: bool = False
    models: tuple[str, ...] = ()
    otherwise: Severity = "info"
    confidence: Literal["high", "med", "low"] = "med"
    judge: str = ""
    category: str = ""
    fires: tuple[str, ...] = ()
    quiet: tuple[str, ...] = ()
    origin: tuple[str, ...] = ()

    def severity_for(self, model: str | None) -> Severity:
        """Say how much a hit matters on a model.

        Args:
            model: The model name, as in ``claude-opus-5-5``; ``None`` when unknown.

        Returns:
            ``severity`` on a listed model or when none are listed, else ``otherwise``.
        """
        if not self.models:
            return self.severity
        if model is not None and any(fnmatch.fnmatchcase(model, glob) for glob in self.models):
            return self.severity
        return self.otherwise

    def describe_severity(self, model: str | None) -> str:
        """Say how much a hit matters: on one model, or across the models it names.

        Args:
            model: The model name; ``None`` to describe every model the rule names.

        Returns:
            ``warn``, or ``error on claude-opus-5; warn elsewhere``.
        """
        if model is not None or not self.models:
            return self.severity_for(model)
        return f"{self.severity} on {', '.join(self.models)}; {self.otherwise} elsewhere"

    def describe_matching(self) -> str:
        """Say in words how this rule turns matches into findings.

        Returns:
            A sentence, as in ``Fires on every match.``
        """
        if self.missing:
            return "Fires when nothing matches: the pattern is what should be there."
        if self.run:
            unit = "sentences" if self.unit == "sentence" else "lines"
            return f"Fires once on {self.run} or more {unit} in a row that match."
        if self.at_least:
            return f"Fires once when the pattern matches {self.at_least} or more times."
        if self.unless:
            return "Fires on every match, unless its sentence or the next gives a reason."
        return "Fires on every match."

    def _flags(self) -> int:
        return re.MULTILINE | (0 if self.case_sensitive else re.IGNORECASE)

    def scan(self, text: str, model: str | None = None) -> list["Finding"]:
        """Find this rule's hits in a text.

        Args:
            text: The text to read.
            model: The target model, for the severity.

        Returns:
            The findings, in order.
        """
        pattern = re.compile(self.pattern, self._flags())
        severity = self.severity_for(model)
        if self.missing:
            if pattern.search(text):
                return []
            return [Finding(self.id, severity, 1, 1, "", self.fix)]
        if self.run:
            return self._runs(text, pattern, severity)
        matches = list(pattern.finditer(text))
        if self.at_least:
            if len(matches) < self.at_least:
                return []
            first = matches[0]
            return [
                _finding(self, severity, text, first.start(), f"{len(matches)} times: {first[0]}")
            ]
        if self.unless:
            reason = re.compile(self.unless, self._flags())
            sentences = _segments(text, "sentence")
            matches = [m for m in matches if not _explained(sentences, m.start(), reason)]
        return [_finding(self, severity, text, m.start(), m[0]) for m in matches]

    def _runs(self, text: str, pattern: re.Pattern[str], severity: Severity) -> list["Finding"]:
        """Fire once per run of ``run`` or more consecutive matching segments."""
        found: list[Finding] = []
        streak: list[tuple[int, str]] = []
        for start, segment in [*_segments(text, self.unit, blanks=True), (len(text), "")]:
            if segment and pattern.search(segment):
                streak.append((start, segment))
                continue
            if len(streak) >= self.run:
                first_start, first = streak[0]
                excerpt = f"{len(streak)} in a row: {first.strip()}"
                found.append(_finding(self, severity, text, first_start, excerpt))
            streak = []
        return found


@dataclass(frozen=True, slots=True)
class Finding:
    """One hit of one rule.

    Attributes:
        rule: The rule's id.
        severity: How much it matters on the target model.
        line: One-based line of the hit.
        column: One-based column of the hit.
        excerpt: The text that matched, shortened.
        fix: What to do about it.
    """

    rule: str
    severity: Severity
    line: int
    column: int
    excerpt: str
    fix: str


@dataclass(frozen=True, slots=True)
class Guidance:
    """How to write for one target, in one category, with where the claims come from.

    Attributes:
        category: The category.
        target: ``<provider>/default`` or ``<provider>/<model>``.
        version: The release its file comes from.
        summary: What matters about the target, in a sentence.
        instructions: What to do when writing a prompt for it.
        sources: Where each claim comes from.
    """

    category: str
    target: str
    version: str
    summary: str
    instructions: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Part:
    """One released file of the core rules that was read.

    Attributes:
        category: The category.
        name: The rule's folder name, or ``examples`` or ``guidance``.
        target: ``default``, ``<provider>/default`` or ``<provider>/<model>``.
        version: The release it changed in.
        released: The day of that release.
        changes: What changed.
    """

    category: str
    name: str
    target: str
    version: str
    released: str
    changes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Release:
    """One release of one category: every file that changed in it, and why.

    Attributes:
        category: The category.
        version: The release, as in ``1.1.0``.
        released: The day of the release.
        changes: Each change, with the files that carry it, as ``show-reasoning anthropic/default``.
    """

    category: str
    version: str
    released: str
    changes: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class Example:
    """A whole prompt, and exactly which rules must fire on it.

    Attributes:
        name: What the example is.
        text: The prompt.
        fires: The ids of every rule that must fire; any other hit is a failure.
        severities: For some of them, the severity every hit must have.
        counts: How many times some of them must fire.
        scope: The text kind it is.
        category: The category whose rules it is linted with.
        model: The model it is linted as, which decides severities.
    """

    name: str
    text: str
    fires: frozenset[str]
    severities: Mapping[str, Severity]
    counts: Mapping[str, int]
    scope: Scope = "prompt"
    category: str = ""
    model: str | None = None


@dataclass(frozen=True, slots=True)
class RulePack:
    """A loaded, checked set of rules for one target.

    Attributes:
        rules: The rules, category by category.
        examples: Whole-prompt examples that still hold for these rules.
        parts: Every core file read.
        guidance: What the vendor and model files say about writing for the target.
        releases: Each category, and the release it was read at.
        target: The model the rules were resolved for, if any.
        project: The project folder laid over them, if any.
    """

    rules: tuple[Rule, ...]
    examples: tuple[Example, ...] = ()
    parts: tuple[Part, ...] = ()
    guidance: tuple[Guidance, ...] = ()
    releases: tuple[tuple[str, str], ...] = ()
    target: Target | None = None
    project: str = ""

    @property
    def version(self) -> str:
        """Name these rules as a report records them, so a result can be reproduced.

        Returns:
            The common release, then any category on another one, then the target and
            the project folder: ``1.0.0 (wording 1.1.0) for anthropic:claude-opus-5-5 + rules/``.
        """
        read = [(category, release) for category, release in self.releases if release]
        if not read:
            return f"{self.project}/" if self.project else "no rules"
        common = Counter(release for _, release in read).most_common(1)[0][0]
        odd = [f"{category} {release}" for category, release in read if release != common]
        named = f"{common} ({', '.join(odd)})" if odd else common
        if self.target is not None:
            named = f"{named} for {self.target[0]}:{self.target[1]}"
        return f"{named} + {self.project}/" if self.project else named

    @property
    def categories(self) -> tuple[str, ...]:
        """Every category in this pack, in order."""
        return tuple(category for category, _ in self.releases)

    @property
    def model(self) -> str | None:
        """The bare model name severities are read for."""
        return None if self.target is None else self.target[1]

    def only(self, categories: Sequence[str]) -> "RulePack":
        """Keep only some categories.

        Args:
            categories: The categories to keep.

        Returns:
            The narrower pack.
        """
        return RulePack(
            tuple(rule for rule in self.rules if rule.category in categories),
            tuple(example for example in self.examples if example.category in categories),
            tuple(part for part in self.parts if part.category in categories),
            tuple(note for note in self.guidance if note.category in categories),
            tuple(pair for pair in self.releases if pair[0] in categories),
            self.target,
            self.project,
        )


@dataclass(frozen=True, slots=True)
class RuleCheck:
    """The outcome of proving one rule, or one example, against its cases.

    Attributes:
        name: The rule's id, or the example's name.
        failures: What went wrong; empty when it passed.
        cases: How many cases were run.
        category: The category it belongs to.
    """

    name: str
    failures: tuple[str, ...]
    cases: int
    category: str = ""

    @property
    def passed(self) -> bool:
        """Whether every case behaved."""
        return not self.failures


def _segments(
    text: str, unit: Literal["sentence", "line"], *, blanks: bool = False
) -> list[tuple[int, str]]:
    """Split text into sentences or lines, each with its offset.

    Blank segments are dropped unless asked for: a run needs them, because a blank
    line ends a list or a paragraph, and so ends the run.
    """
    boundary = _SENTENCE if unit == "sentence" else re.compile(r"\n")
    out: list[tuple[int, str]] = []
    start = 0
    for match in boundary.finditer(text):
        out.append((start, text[start : match.start()]))
        start = match.end()
    out.append((start, text[start:]))
    return [(offset, segment) for offset, segment in out if blanks or segment.strip()]


def _explained(sentences: list[tuple[int, str]], offset: int, reason: re.Pattern[str]) -> bool:
    """Report whether the sentence holding an offset, or the next one, gives a reason."""
    index = max((i for i, (start, _) in enumerate(sentences) if start <= offset), default=0)
    nearby = " ".join(segment for _, segment in sentences[index : index + 2])
    return reason.search(nearby) is not None


def _finding(rule: Rule, severity: Severity, text: str, offset: int, excerpt: str) -> Finding:
    """Build a finding at an offset, with its line and column.

    A pattern that has to match the boundary before a sentence -- ``. Do not`` --
    reports from the first word, so the column points at what to change.
    """
    lead = len(excerpt) - len(excerpt.lstrip(" \t\n.;:!?-*"))
    if excerpt[lead:].strip():  # a match that is all punctuation, like "!!", stays whole
        offset, excerpt = offset + lead, excerpt[lead:]
    line = text.count("\n", 0, offset) + 1
    column = offset - (text.rfind("\n", 0, offset) + 1) + 1
    flat = " ".join(excerpt.split())
    short = flat if len(flat) <= 60 else f"{flat[:57]}..."
    return Finding(rule.id, severity, line, column, short, rule.fix)
