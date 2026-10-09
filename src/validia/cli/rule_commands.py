"""The rule commands: lint, and listing, proving, explaining and writing rules."""

import argparse
import difflib
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..rules import (
    CATEGORIES,
    PROJECT_RULES,
    SEVERITIES,
    Guidance,
    Rule,
    RuleCheck,
    Scope,
    check_for,
    core_categories,
    core_releases,
    core_targets,
    core_versions,
    lint_text,
    verify_core,
    verify_project,
    verify_rules,
)
from ..rules.local import (
    Written,
    disable_rule,
    extend_rule,
    fall_back,
    new_rule,
    replace_rule,
    rule_target,
)
from ..runs.access import (
    parse_model,
)
from ..suites.suite import (
    Suite,
    load_suite,
)
from .common import CliError, CommandBase, Commands, _count
from .interview import (
    regex_problem,
)
from .settings import SETTINGS_FILE

_NEEDED = "an answer is needed"


def _print_failures(checks: Sequence[RuleCheck]) -> int:
    """Print every check that failed, and say how many."""
    failed = [check for check in checks if not check.passed]
    for check in failed:
        print(f"FAIL  {check.name}")
        for failure in check.failures:
            print(f"      {failure}")
    return len(failed)


def _released(rule: Rule) -> list[str]:
    """Every release a core rule changed in, target by target; nothing for a project rule."""
    lines = []
    for target in core_targets(rule.category):
        versions = core_versions(rule.category, rule.id.split("/", 1)[1], target)
        if versions:
            lines.append(f"{target} {', '.join(versions)}")
    return lines


def _print_guidance(guidance: Guidance, *, sources: bool, stream: Any = None) -> None:
    """Print one target's instructions, to stderr unless told otherwise."""
    out = sys.stderr if stream is None else stream
    where = f"{guidance.category}/{guidance.target} {guidance.version}"
    print(f"{where}: {guidance.summary}", file=out)
    for instruction in guidance.instructions:
        print(f"  - {instruction}", file=out)
    if sources:
        for source in guidance.sources:
            print(f"    source: {source}", file=out)


class RuleCommands(CommandBase):
    """``lint``, and the ``rules`` actions that list, prove, explain and write rules."""

    def _rules_parsers(self, commands: Commands, common: argparse.ArgumentParser) -> None:
        """Add the commands of this family: lint, and every rules action."""
        # Which rules a command reads: some categories, for the model in settings or -m.
        # Versions are settings (lint.rules.<category>), so --set pins one for a run.
        pack_options = argparse.ArgumentParser(add_help=False)
        pack_options.add_argument(
            "--category",
            action="append",
            metavar="CATEGORY",
            help=(
                f"only this category's rules; repeat for more ({', '.join(CATEGORIES)},"
                f" or one of your own in {PROJECT_RULES}/)"
            ),
        )
        model_option = argparse.ArgumentParser(add_help=False)
        model_option.add_argument(
            "-m",
            "--model",
            help=(
                "the model the prompt is for, as provider:model: its vendor and model"
                " files set severities and add rules (setting: model)"
            ),
        )

        lint = commands.add_parser(
            "lint",
            parents=[common, pack_options, model_option],
            help="check prompts against the rules for a model, without calling it",
            description=(
                "Check prompt files, or whole suites -- their prompt and their tool"
                " descriptions -- against the rules for a model: each rule's default"
                " file, then its vendor's and its model's, then the project's"
                f" {PROJECT_RULES}/ folder. Exits 1 when a finding reaches --fail-on. Pin"
                " a category to a release with --set lint.rules.wording=1."
            ),
        )
        lint.add_argument(
            "targets",
            nargs="+",
            type=Path,
            metavar="PROMPT_OR_SUITE",
            help="prompt files, or suite.toml files",
        )
        lint.add_argument(
            "--fail-on",
            choices=("error", "warning"),
            help="lowest severity that fails (setting: lint.fail_on)",
        )
        lint.add_argument(
            "--no-guidance",
            action="store_true",
            help="leave out the model's instructions for the categories with findings",
        )
        lint.add_argument(
            "--rules",
            action="append",
            metavar="NAME",
            help=(
                "only these rules: a category, a rule id (wording/capitals), or regex or"
                " model for how rules decide; repeat for more (default: a suite's"
                " [rules] run)"
            ),
        )
        lint.set_defaults(handler=self._lint)

        rules = commands.add_parser(
            "rules",
            help="show, prove and compare rules, and read a model's instructions",
            description=(
                "Work with the rules: list the ones a model is linted with, prove each"
                " against its own cases, see each category's releases, explain one rule,"
                " or read the prompting instructions a model's files carry. A"
                f" {PROJECT_RULES}/ folder in the project is laid over the core rules."
            ),
        )
        actions = rules.add_subparsers(dest="action", required=True, metavar="ACTION")
        listing = actions.add_parser(
            "list",
            parents=[common, pack_options, model_option],
            help="show every rule in use, as a model sees it",
        )
        listing.set_defaults(handler=self._rules_list)
        testing = actions.add_parser(
            "test",
            parents=[common, pack_options, model_option],
            help="prove every rule and example against its own cases",
        )
        testing.add_argument(
            "--all",
            action="store_true",
            help=(
                "prove every core target validia ships, each over the files beneath it,"
                f" and every target the project's {PROJECT_RULES}/ folder has files for"
            ),
        )
        testing.add_argument(
            "--target",
            action="append",
            metavar="TARGET",
            help="with --all, only this target, as in anthropic/claude-opus-5-5; repeat for more",
        )
        testing.set_defaults(handler=self._rules_test)
        versions = actions.add_parser(
            "versions",
            parents=[pack_options],
            help="show each category's releases and what changed in them",
        )
        versions.set_defaults(handler=self._rules_versions)
        guidance = actions.add_parser(
            "guidance",
            parents=[common, pack_options, model_option],
            help="show a model's prompting instructions, target by target, with sources",
        )
        guidance.set_defaults(handler=self._rules_guidance)
        explain = actions.add_parser(
            "explain",
            parents=[common, model_option],
            help="show one rule as a model sees it: what it finds, its fix and its cases",
        )
        explain.add_argument(
            "rule",
            metavar="RULE",
            help="the rule's id, as in reasoning/show-reasoning, or its name if only one has it",
        )
        explain.set_defaults(handler=self._rules_explain)

        # Writing the project's own rule files: where each one goes.
        where = argparse.ArgumentParser(add_help=False)
        where.add_argument(
            "-m",
            "--model",
            dest="for_model",
            metavar="MODEL",
            help="write it for this model only: rules/<category>/<name>/<provider>/<model>/",
        )
        where.add_argument(
            "--vendor",
            action="store_true",
            help="with -m, write it for every model of that model's provider instead",
        )
        where.add_argument(
            "--target",
            help="the folder to write in: default, <provider>/default or <provider>/<model>",
        )
        where.add_argument("--version", help="the file's version; 1.0.0 for a folder's first file")
        fields = argparse.ArgumentParser(add_help=False)
        fields.add_argument("--title", help="what it finds, in a few words")
        fields.add_argument("--pattern", help="the regular expression it looks for")
        fields.add_argument("--fix", help="what to do about a hit")
        fields.add_argument("--severity", choices=SEVERITIES, help="how much a hit matters")
        fields.add_argument(
            "--models", nargs="+", metavar="GLOB", help="the models the severity is for"
        )
        fields.add_argument(
            "--fires", action="append", default=[], metavar="TEXT", help="text it must fire on"
        )
        fields.add_argument(
            "--quiet",
            action="append",
            default=[],
            metavar="TEXT",
            help="text it must stay quiet on",
        )
        rule_arg = argparse.ArgumentParser(add_help=False)
        rule_arg.add_argument("rule", metavar="RULE", help="the rule's id, as in wording/capitals")
        extend = actions.add_parser(
            "extend",
            parents=[common, rule_arg, where, fields],
            help=f"extend a rule in {PROJECT_RULES}/: change some fields, add cases",
        )
        extend.set_defaults(handler=self._rules_extend)
        overwrite = actions.add_parser(
            "replace",
            parents=[common, rule_arg, where],
            help=f"copy a rule into {PROJECT_RULES}/ as a full definition, to edit",
        )
        overwrite.set_defaults(handler=self._rules_replace)
        disable = actions.add_parser(
            "disable",
            parents=[common, rule_arg, where],
            help=f"turn a rule off in {PROJECT_RULES}/",
        )
        disable.set_defaults(handler=self._rules_disable)
        fallback = actions.add_parser(
            "fallback",
            parents=[common, rule_arg],
            help=f"keep one core rule as an older release had it, in {PROJECT_RULES}/",
        )
        fallback.add_argument("--to", required=True, metavar="RELEASE", help='as in "1" or "1.0.0"')
        fallback.add_argument(
            "--version", help="the file's version; 1.0.0 for a folder's first file"
        )
        fallback.set_defaults(handler=self._rules_fallback)
        new = actions.add_parser(
            "new",
            parents=[common, rule_arg, where, fields],
            help=f"add a rule of your own to {PROJECT_RULES}/, asking for what is missing",
        )
        new.add_argument(
            "--tools", action="store_true", help="it reads tool descriptions, not the prompt"
        )
        new.set_defaults(handler=self._rules_new)

    def _lint(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Check prompts, or suites' prompts and tools, against the rules for a model."""
        self._require_files(args.targets)
        flags = {"lint.fail_on": args.fail_on, "model": args.model}
        totals: Counter[str] = Counter()
        failing = 0
        versions: set[str] = set()
        advice: dict[tuple[str, str], Guidance] = {}
        worst: tuple[int, str] | None = None  # the finding to point at: most severe, first
        for target in args.targets:
            is_suite = target.suffix == ".toml"
            _, settings = self._settings(args, argv, flags, suite=target if is_suite else None)
            suite = load_suite(self.cwd / target) if is_suite else None
            # --rules, else the suite's [rules] run, picks the rules; --category narrows them.
            names = args.rules or (suite.rules if suite is not None else None)
            pack = self._pack(settings, args.category, names)
            versions.add(pack.version)
            owner = {rule.id: rule.category for rule in pack.rules}
            threshold = SEVERITIES.index("warn" if settings.lint.fail_on == "warning" else "error")
            hit: set[str] = set()
            for location, text, scope in self._lint_texts(target, suite):
                for finding in lint_text(text, pack, scope=scope):
                    totals[finding.severity] += 1
                    failing += SEVERITIES.index(finding.severity) >= threshold
                    hit.add(owner[finding.rule])
                    rank = SEVERITIES.index(finding.severity)
                    if worst is None or rank > worst[0]:
                        worst = (rank, finding.rule)
                    print(
                        f"{location}:{finding.line}:{finding.column}  {finding.severity:<5}  "
                        f"{finding.rule}  {finding.excerpt!r}  {finding.fix}"
                    )
            for note in pack.guidance:
                if note.category in hit:
                    advice.setdefault((note.category, note.target), note)
        if advice and not args.no_guidance:
            for note in advice.values():
                _print_guidance(note, sources=False)
        if worst is not None:
            print(f"explain: validia rules explain {worst[1]}", file=sys.stderr)
        found = sum(totals.values())
        counts = ", ".join(f"{totals[s]} {s}" for s in reversed(SEVERITIES) if totals[s])
        summary = (
            f"{found} finding{'s' if found != 1 else ''} ({counts})" if found else "no findings"
        )
        print(f"rules {', '.join(sorted(versions))}: {summary}", file=sys.stderr)
        return 1 if failing else 0

    def _lint_texts(self, target: Path, suite: Suite | None) -> list[tuple[str, str, Scope]]:
        """The texts to check for one target: a prompt, or a suite's prompt and tools."""
        if suite is None:
            path = self.cwd / target
            return [(self._shown(path), path.read_text(encoding="utf-8"), "prompt")]
        return self._suite_texts(suite, self.cwd)

    def _rules_list(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Show every rule in use, with its severity on the model when one is given."""
        _, settings = self._settings(args, argv, {"model": args.model})
        pack = self._pack(settings, args.category)
        print(f"rules {pack.version}", file=sys.stderr)
        if not pack.rules:
            return 0
        width = max(len(rule.id) for rule in pack.rules)
        for rule in pack.rules:
            scope = "tools" if rule.scope == ("tool_description",) else "prompt"
            print(
                f"{rule.id:<{width}}  {rule.severity_for(pack.model):<5}  "
                f"{scope:<6}  {rule.check:<5}  {rule.title or rule.fix}"
            )
            if rule.models and pack.model is None:
                print(f"{'':<{width}}  on {', '.join(rule.models)}; {rule.otherwise} elsewhere")
        return 0

    def _write_target(self, args: argparse.Namespace) -> str:
        """The folder a rule file goes in, from --target, or -m and --vendor."""
        if args.target and args.for_model:
            msg = "give --target or -m, not both"
            raise CliError(msg)
        if args.target:
            rule_target(args.target)
            return str(args.target)
        if args.for_model:
            spec = parse_model(args.for_model)
            return f"{spec.provider}/default" if args.vendor else f"{spec.provider}/{spec.name}"
        if args.vendor:
            msg = "--vendor needs -m MODEL, to know which provider"
            raise CliError(msg)
        return "default"

    def _report(self, written: Written, target: str) -> int:
        """Say what a write left, how the rule now reads, and that it holds."""
        for path in written.paths:
            print(f"wrote {self._shown(path)}")
        rule_id = written.paths[0].relative_to(self.cwd / PROJECT_RULES).parts
        name = f"{rule_id[0]}/{rule_id[1]}"
        rule = next((item for item in written.pack.rules if item.id == name), None)
        on = "every model" if target == "default" else target
        one_model = target != "default" and not target.endswith("/default")
        if rule is None:
            print(f"{name} is off on {on}")
        else:
            severity = rule.describe_severity(written.pack.model if one_model else None)
            print(f"{name} on {on}: {severity}")
            print(f"  read as {' -> '.join(rule.origin)}")
        if check_for(rule_id[0]) == "model":
            print(f"  its cases wait for a model: every {rule_id[0]} rule is judged by one")
            return 0
        cases = sum(check.cases for check in written.checks if check.name == name)
        examples = sum(1 for check in written.checks if check.name != name)
        print(f"  proved: {_count(cases, 'case')} and {_count(examples, 'example')} pass")
        return 0

    def _pins(self, args: argparse.Namespace, argv: Sequence[str]) -> dict[str, str]:
        _, settings = self._settings(args, argv)
        return settings.lint.rules.pinned()

    def _rules_extend(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Extend a rule in the project: some fields, more cases."""
        target = self._write_target(args)
        changes = {
            key: value
            for key in ("title", "pattern", "fix", "severity", "models")
            if (value := getattr(args, key)) is not None
        }
        written = extend_rule(
            self.cwd / PROJECT_RULES,
            args.rule,
            target=target,
            fields=changes,
            fires=args.fires,
            quiet=args.quiet,
            version=args.version,
            pins=self._pins(args, argv),
        )
        return self._report(written, target)

    def _rules_replace(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Copy a rule into the project as a full definition, to edit."""
        target = self._write_target(args)
        written = replace_rule(
            self.cwd / PROJECT_RULES,
            args.rule,
            target=target,
            version=args.version,
            pins=self._pins(args, argv),
        )
        self._report(written, target)
        print(f"  edit {self._shown(written.paths[0])}; its cases are beside it")
        return 0

    def _rules_disable(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Turn a rule off in the project."""
        target = self._write_target(args)
        written = disable_rule(
            self.cwd / PROJECT_RULES,
            args.rule,
            target=target,
            version=args.version,
            pins=self._pins(args, argv),
        )
        return self._report(written, target)

    def _rules_fallback(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Keep one core rule as an older release had it."""
        written = fall_back(
            self.cwd / PROJECT_RULES,
            args.rule,
            args.to,
            version=args.version,
            pins=self._pins(args, argv),
        )
        return self._report(written, "default")

    def _rules_new(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Add a rule of the project's own, asking for whatever the flags leave out."""
        target = self._write_target(args)
        missing = [
            flag
            for flag, value in (
                ("--pattern", args.pattern),
                ("--fix", args.fix),
                ("--fires", args.fires),
                ("--quiet", args.quiet),
            )
            if not value
        ]
        if missing and not self.interactive:
            msg = f"a new rule needs {', '.join(missing)}"
            raise CliError(msg)
        pattern = args.pattern or self.ask.ask(
            "Pattern, a regular expression",
            valid=lambda text: regex_problem(text) if text else _NEEDED,
        )
        fix = args.fix or self.ask.ask(
            "What to do about a hit", valid=lambda text: None if text else _NEEDED
        )
        title = (
            args.title
            if args.title is not None or not missing
            else self.ask.ask("Title, what it finds in a few words (optional)")
        )
        fires = args.fires or self._some("Text it must fire on")
        quiet = args.quiet or self._some("Text it must stay quiet on")
        values: dict[str, Any] = {"title": title, "pattern": pattern, "fix": fix}
        if args.severity:
            values["severity"] = args.severity
        if args.models:
            values["models"] = args.models
        if args.tools:
            values["scope"] = ["tool_description"]
        written = new_rule(
            self.cwd / PROJECT_RULES,
            args.rule,
            fields={key: value for key, value in values.items() if value},
            fires=fires,
            quiet=quiet,
            target=target,
            version=args.version,
            pins=self._pins(args, argv),
        )
        return self._report(written, target)

    def _some(self, question: str) -> list[str]:
        """Ask for one or more answers, until an empty one."""
        while not (answers := self.ask.many(f"{question} (empty to finish)")):
            print("  give at least one", file=sys.stderr)
        return answers

    def _rules_explain(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Show one rule as the model sees it: what it finds, its fix and its cases."""
        _, settings = self._settings(args, argv, {"model": args.model})
        pack = self._pack(settings)
        wanted = args.rule.lower()
        named = [r for r in pack.rules if wanted in (r.id, r.id.split("/", 1)[1])]
        if len(named) > 1:
            msg = f"{args.rule!r} names {len(named)} rules: {', '.join(r.id for r in named)}"
            raise CliError(msg)
        rule = named[0] if named else None
        if rule is None:
            close = difflib.get_close_matches(args.rule, [r.id for r in pack.rules], n=1)
            hint = f" - did you mean {close[0]!r}?" if close else ""
            msg = f"no rule {args.rule!r} in {pack.version}{hint}"
            raise CliError(msg)
        scope = "tool descriptions" if rule.scope == ("tool_description",) else "the prompt"
        on = f" on {settings.model}" if pack.model else ""
        rows = [
            ("category", f"{rule.category}, reads {scope}"),
            (
                "decides",
                f"by a model, as every {rule.category} rule does: its pattern only finds"
                " candidates; skipped without a model"
                if rule.check == "model"
                else "by its pattern alone (regex); runs without a model",
            ),
            ("severity", f"{rule.describe_severity(pack.model)}{on}"),
            ("fix", rule.fix),
            ("matches", rule.describe_matching()),
            ("pattern", rule.pattern),
            *([("unless", rule.unless)] if rule.unless else []),
            *[("fires on" if i == 0 else "", repr(case)) for i, case in enumerate(rule.fires)],
            *[("quiet on" if i == 0 else "", repr(case)) for i, case in enumerate(rule.quiet)],
            ("read as", " -> ".join(rule.origin)),
            *[("released" if i == 0 else "", line) for i, line in enumerate(_released(rule))],
        ]
        print(f"{rule.id}: {rule.title}" if rule.title else rule.id)
        for name, value in rows:
            print(f"  {name:<10}  {value}")
        return 0

    def _rules_test(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Prove the rules in use, or with ``--all`` every core layer in its own chain."""
        if args.target and not args.all:
            msg = "--target picks targets to prove with --all"
            raise CliError(msg)
        _, settings = self._settings(args, argv, {"model": args.model})
        if args.all:
            pins = settings.lint.rules.pinned()
            found = verify_core(pins=pins, categories=args.category, targets=args.target)
            proved = [
                (f"{category}/{target} {pack.releases[0][1]}", pack, checks)
                for category, target, pack, checks in found
            ]
            matched = {target for _, target, _, _ in found}
            project = self.cwd / PROJECT_RULES
            if project.is_dir():
                for target, pack, checks in verify_project(project, pins=pins):
                    if args.target and target not in args.target:
                        continue
                    if args.category:
                        pack = pack.only(args.category)
                        checks = verify_rules(pack)
                    matched.add(target)
                    proved.append((f"{PROJECT_RULES}/ {target}", pack, checks))
            unmatched = sorted(set(args.target or ()) - matched)
            if unmatched or not proved:
                msg = f"no target matches: {', '.join(unmatched)}"
                raise CliError(msg)
        else:
            pack = self._pack(settings, args.category)
            proved = [(f"rules {pack.version}", pack, verify_rules(pack))]
        failed = 0
        for name, pack, checks in proved:
            failed += _print_failures(checks)
            ran = [check for check in checks if not check.waiting]
            passed = sum(check.passed for check in ran)
            cases = sum(check.cases for check in ran)
            regex = sum(1 for rule in pack.rules if rule.check == "regex")
            examples = sum(1 for check in ran) - regex
            waiting = len(checks) - len(ran)
            wait = f"{waiting} model rules and examples wait for a model"
            if not ran:
                print(f"{name}: {wait}")
                continue
            print(
                f"{name}: {passed} of {len(ran)} pass"
                f" ({_count(regex, 'regex rule')}, {_count(examples, 'example')},"
                f" {_count(cases, 'case')})" + (f"; {wait}" if waiting else "")
            )
        return 1 if failed else 0

    def _rules_versions(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Show every release of every core category, newest first, with what changed."""
        known = core_categories()
        unknown = [name for name in args.category or () if name not in known]
        if unknown:
            msg = f"no core category {unknown[0]!r}; there are {', '.join(known)}"
            raise CliError(msg)
        for category in args.category or known:
            releases = core_releases(category)
            print(category)
            for release in reversed(releases):
                latest = "  (latest)" if release is releases[-1] else ""
                print(f"  {release.version}  released {release.released}{latest}")
                for change, files in release.changes:
                    where = ", ".join(files) if len(files) <= 3 else f"{len(files)} files"
                    print(f"     - {change}  ({where})")
        return 0

    def _rules_guidance(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Show the prompting instructions a model's layers carry, with their sources."""
        _, settings = self._settings(args, argv, {"model": args.model})
        if settings.model is None:
            msg = f"no model to show guidance for: pass --model, or set `model` in {SETTINGS_FILE}"
            raise CliError(msg)
        pack = self._pack(settings, args.category)
        if not pack.guidance:
            print(
                f"no guidance for {settings.model} in these rules ({pack.version})", file=sys.stderr
            )
            return 0
        for note in pack.guidance:
            _print_guidance(note, sources=True, stream=sys.stdout)
        return 0
