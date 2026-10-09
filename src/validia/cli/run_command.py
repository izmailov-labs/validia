"""The run command: send a suite to a model, grade every reply, report and record."""

import argparse
import functools
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from franca.core.clock import AsyncioClock
from franca.core.ids import Provider
from franca.core.settings import Settings as FrancaSettings
from franca.core.settings import SettingsKeyProvider
from franca.core.transport import Transport

from .. import __version__
from ..rules import SEVERITIES, Finding, RuleError, RulePack, lint_text
from ..runs.access import (
    parse_model,
)
from ..runs.runner import (
    FATAL,
    RunSummary,
    TrialResult,
    build_model,
    run_trial,
    summarize,
    trials,
    unsupported,
)
from ..suites.suite import Suite, SuiteError
from ._loop import run_bounded
from .common import CliError, CommandBase, Commands, _count
from .output import Output
from .settings import SETTINGS_FILE, Settings


def _count_arg(text: str) -> int:
    """Parse a count that may be zero."""
    try:
        value = int(text)
    except ValueError:
        msg = f"expected a whole number, got {text!r}"
        raise argparse.ArgumentTypeError(msg) from None
    if value < 0:
        msg = f"must be 0 or more, got {value}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _positive_int(text: str) -> int:
    """Parse a count that has to be at least one."""
    try:
        value = int(text)
    except ValueError:
        msg = f"expected a whole number, got {text!r}"
        raise argparse.ArgumentTypeError(msg) from None
    if value < 1:
        msg = f"must be at least 1, got {value}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _outcome(runs: Sequence[TrialResult], reps: int) -> tuple[str, str]:
    """Judge a case once all its trials are in: PASS, FAIL or ERROR, and why.

    A wrong answer outranks a failed call: FAIL says the prompt got it wrong at
    least once, ERROR only that some call never came back to be graded.
    """
    failed = [run for run in runs if run.passed is False]
    errors = [run for run in runs if run.passed is None]
    status = "FAIL" if failed else "ERROR" if errors else "PASS"
    why = failed[0].reason if failed else f"{errors[0].error}: {errors[0].reason}" if errors else ""
    passed = sum(1 for run in runs if run.passed)
    tally = f"{passed}/{len(runs)}" if reps > 1 else ""
    return status, "  ".join(part for part in (tally, why) if part)


def _report(out: Output, name: str, model: str, summary: RunSummary) -> None:
    """Print a run's summary: the pass rate first, then what explains it."""
    low, high = summary.interval
    rate = summary.rate
    shown = "no replies to grade" if rate is None else f"{rate:.1%}"
    clean = rate == 1 and not summary.errors
    out.line(
        (
            f"{name} on {model}: {summary.passed} of {summary.graded} passed, {shown}"
            + (f" (95% interval {low:.1%}-{high:.1%})" if rate is not None else ""),
            "bold green" if clean else "bold red",
        )
    )
    if summary.served_models:
        out.line(f"  served by {', '.join(summary.served_models)}")
    if len(summary.groups) > 1:
        groups = ", ".join(f"{group} {p}/{g}" for group, (p, g) in summary.groups.items())
        out.line(f"  by group: {groups}")
    if summary.errors:
        kinds = ", ".join(f"{kind} {count}" for kind, count in summary.errors.items())
        out.line((f"  errors: {sum(summary.errors.values())} ({kinds})", "yellow"))
    cached = f" ({summary.cache_read_tokens:,} cached)" if summary.cache_read_tokens else ""
    out.line(
        (f"  tokens: {summary.input_tokens:,} in{cached}, {summary.output_tokens:,} out", "dim")
    )
    out.line(
        (
            f"  latency: p50 {summary.latency_p50_ms:,.0f} ms,"
            f" p95 {summary.latency_p95_ms:,.0f} ms",
            "dim",
        )
    )


class RunCommand(CommandBase):
    """The ``run`` command: send a suite to a model, grade every reply, report and record it."""

    def _run_parsers(self, commands: Commands, common: argparse.ArgumentParser) -> None:
        """Add the commands of this family: run."""
        run = commands.add_parser(
            "run",
            parents=[common],
            help="run an evaluation suite against a model, and grade every reply",
            description="Run an evaluation suite against a model, then grade and report it.",
        )
        run.add_argument("suite", type=Path, help="the suite: a prompt and the cases to run it on")
        run.add_argument("-m", "--model", help="the model under evaluation (setting: model)")
        run.add_argument(
            "-n",
            "--reps",
            type=_positive_int,
            help="repetitions of every case (setting: run.reps)",
        )
        run.add_argument(
            "-j",
            "--concurrency",
            type=_positive_int,
            help="cases in flight at once (setting: run.concurrency)",
        )
        run.add_argument(
            "--retries",
            type=_count_arg,
            help="extra attempts for a rate limit or a dropped connection (setting: run.retries)",
        )
        run.add_argument(
            "--fail-under",
            type=float,
            metavar="PERCENT",
            help="exit 1 when the pass rate is below this (setting: run.fail_under)",
        )
        run.add_argument(
            "-o",
            "--output",
            type=Path,
            metavar="DIR",
            help="where results are written (setting: run.output)",
        )
        run.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "test the prompt against the prompt rules, list every case and what it"
                " expects, and send nothing"
            ),
        )
        run.add_argument(
            "--rules",
            action="append",
            metavar="NAME",
            help=(
                "test the prompt with only these rules: a category (wording), a rule id"
                " (wording/capitals), or regex or model for how rules decide; repeat for"
                " more (default: the suite's [rules] run)"
            ),
        )
        run.set_defaults(handler=self._run)

    def _run(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Run a suite: every case, every repetition, graded, written and reported."""
        flags = {
            "model": args.model,
            "run.reps": args.reps,
            "run.concurrency": args.concurrency,
            "run.retries": args.retries,
            "run.fail_under": args.fail_under,
            "run.output": None if args.output is None else args.output.as_posix(),
        }
        out = Output()
        suite = self._checked(out, args.suite)
        _, settings = self._settings(args, argv, flags, suite=args.suite)
        if args.dry_run:
            return self._dry_run(out, suite, settings, args.rules)
        if settings.model is None:
            msg = f"no model to evaluate: pass --model, or set `model` in {SETTINGS_FILE}"
            raise CliError(msg)
        spec = parse_model(settings.model)
        keys = self._keys()
        if self._access(spec.provider, settings, keys) is None:
            raise CliError(self._no_key(spec.provider, settings))
        refused = unsupported(suite)
        if refused is not None:
            raise CliError(refused)
        transport = self._transport()
        clock = self.clock or AsyncioClock()
        model = build_model(
            spec.provider,
            spec.name,
            keys=SettingsKeyProvider(
                FrancaSettings(
                    providers={Provider(name): row for name, row in settings.providers.items()}
                ),
                environ=keys,
            ),
            transport=transport,
            clock=clock,
            settings=settings.providers.get(spec.provider),
        )
        prompt = suite.prompt.read_text(encoding="utf-8")
        plan = trials(suite, settings.run.reps)
        name = suite.path.parent.name
        print(
            f"running {name} on {spec.provider}:{spec.name}: {len(suite.cases)} cases x"
            f" {settings.run.reps} = {_count(len(plan), 'trial')},"
            f" {settings.run.concurrency} at a time",
            file=sys.stderr,
        )
        reps = settings.run.reps
        width = max(len(case.id) for case in suite.cases)
        landed: dict[str, list[TrialResult]] = {}
        with out.progress(len(plan), f"{name} on {spec.provider}:{spec.name}") as advance:

            def progress(result: TrialResult) -> None:
                advance()
                runs = landed.setdefault(result.case, [])
                runs.append(result)
                if len(runs) == reps:
                    status, why = _outcome(runs, reps)
                    out.row(status, result.case, width, why)

            results = run_bounded(
                [
                    functools.partial(
                        run_trial,
                        model,
                        suite,
                        prompt,
                        trial,
                        clock=clock,
                        retries=settings.run.retries,
                    )
                    for trial in plan
                ],
                limit=settings.run.concurrency,
                done=progress,
                stop=lambda result: result.error in FATAL,
                close=transport.aclose,
            )
        out.line()
        summary = summarize(results)
        folder = self._record(
            settings.run.output,
            name,
            clock.wall(),
            results,
            summary,
            {
                "suite": self._shown(suite.path),
                "model": f"{spec.provider}:{spec.name}",
                "reps": settings.run.reps,
                "validia": __version__,
            },
        )
        _report(out, name, f"{spec.provider}:{spec.name}", summary)
        out.line((f"  written to {self._shown(folder)}/", "dim"))
        under = settings.run.fail_under
        fatal = next((result for result in results if result.error in FATAL), None)
        if fatal is not None:
            skipped = len(plan) - len(results)
            print(
                f"validia: stopped at the first {fatal.error} failure, which every trial would"
                f" hit: {fatal.reason.rstrip('.')}"
                + (f"; {_count(skipped, 'trial')} not sent" if skipped else ""),
                file=sys.stderr,
            )
            return 1
        if summary.errors:
            print(
                f"validia: {_count(sum(summary.errors.values()), 'trial')} got no reply",
                file=sys.stderr,
            )
            return 1
        if under is not None and (summary.rate or 0.0) * 100 < under:
            print(f"validia: the pass rate is below {under:g}%", file=sys.stderr)
            return 1
        return 0

    def _checked(self, out: Output, path: Path) -> Suite:
        """Load the suite; when it is broken, list each problem under the case it is in."""
        try:
            return self._suite(path)
        except SuiteError as exc:
            if exc.problems == [str(exc)]:
                raise  # not TOML at all: there are no cases to list
            rows = [("suite", problem) for problem in exc.general]
            rows += [(case, problem) for case, found in exc.cases.items() for problem in found]
            width = max(len(name) for name, _ in rows)
            for name, problem in rows:
                out.row("ERROR", name, width, problem)
            count = len(exc.problems)
            msg = f"{self._shown(path)}: {_count(count, 'problem')}; fix {'it' if count == 1 else 'them'} before running"
            raise CliError(msg) from None

    def _dry_run(
        self, out: Output, suite: Suite, settings: Settings, chosen: Sequence[str] | None
    ) -> int:
        """Test the prompt against its rules, list every case, and say what a run would send.

        The rules need no model -- they read the prompt -- so they run here as tests;
        the cases are only listed. Nothing is sent.
        """
        pack = self._suite_rules(suite, settings, chosen)
        cases = _count(len(suite.cases), "case")
        out.line((f"{self._shown(suite.path)}: {cases}, graded as {suite.summary()}", "bold"))
        out.line()
        tally, failed = self._rule_tests(
            out, suite, pack, settings.lint.fail_on, has_model=settings.model is not None
        )
        out.line(("cases", "bold"))
        width = max(len(case.id) for case in suite.cases)
        for case in suite.cases:
            out.row("ready", case.id, width, case.expected.describe())
        out.line()
        if tally:
            counts = ", ".join(f"{count} {kind}" for kind, count in tally.items() if count)
            out.line((f"rules {pack.version}: ", "bold"), counts)
        ready = (f"{cases} ready", "bold cyan")
        if settings.model is None:
            out.line(
                ready,
                f"; nothing sent. To run them, pass --model, or set `model` in {SETTINGS_FILE}.",
            )
        else:
            spec = parse_model(settings.model)
            source = self._access(spec.provider, settings, self._keys())
            trials = _count(len(suite.cases) * settings.run.reps, "trial")
            out.line(
                ready,
                f"; nothing sent. A run sends {trials} to {spec.provider}:{spec.name},"
                f" {settings.run.concurrency} at a time, "
                + (f"key from {source}." if source else "but no API key is set."),
            )
            if source is None:
                raise CliError(self._no_key(spec.provider, settings))
        if failed:
            msg = (
                f"{_count(len(failed), 'prompt rule')} failed at lint.fail_on ="
                f" {settings.lint.fail_on!r}; `validia rules explain {failed[0]}` says why"
                " and how to fix it"
            )
            raise CliError(msg)
        return 0

    def _suite_rules(
        self, suite: Suite, settings: Settings, chosen: Sequence[str] | None
    ) -> RulePack:
        """The rules to test a suite's prompt with: ``--rules``, else its ``[rules] run``."""
        if chosen:
            return self._pack(settings, names=chosen)
        try:
            return self._pack(settings, names=suite.rules)
        except RuleError as exc:  # name the file the names came from, not "the rules chosen"
            raise RuleError(f"{self._shown(suite.path)} [rules] run", exc.problems) from None

    def _rule_tests(
        self, out: Output, suite: Suite, pack: RulePack, fail_on: str, *, has_model: bool
    ) -> tuple[dict[str, int], list[str]]:
        """Run each rule over the suite's prompt and tools, and print it as a test.

        A ``regex`` rule passes when it finds nothing; one that finds something fails
        at ``fail_on`` and warns or informs below it. A ``model`` rule needs a model:
        without one it is skipped, and with one what its pattern finds is a candidate
        to ``CHECK`` -- until the judge that confirms candidates is built, nothing more.
        A rule that reads only tool descriptions, in a suite without tools, is skipped.

        Returns:
            How many tests came to each outcome, and the ids of the rules that failed.
        """
        texts = self._suite_texts(suite, suite.path.parent)
        read = {scope for _, _, scope in texts}
        found: dict[str, list[tuple[str, Finding]]] = {}
        for location, text, scope in texts:
            for finding in lint_text(text, pack, scope=scope):
                found.setdefault(finding.rule, []).append((location, finding))
        threshold = SEVERITIES.index("warn" if fail_on == "warning" else "error")
        kinds = ("passed", "failed", "warnings", "info", "to check", "skipped")
        tally = dict.fromkeys(kinds, 0)
        failed: list[str] = []
        width = max((len(rule.id.partition("/")[2]) for rule in pack.rules), default=0)
        category = ""
        for rule in pack.rules:
            if rule.category != category:
                category = rule.category
                out.line((category, "bold"))
            name = rule.id.partition("/")[2]
            tag = f"{rule.check:<5}  "
            if rule.check == "model" and not has_model:
                out.row("SKIP", name, width, f"{tag}needs a model: pass --model")
                tally["skipped"] += 1
                continue
            hits = found.get(rule.id, [])
            if not hits:
                skipped = not read & set(rule.scope)
                about = "no tools in this suite" if skipped else rule.title or rule.fix
                out.row("SKIP" if skipped else "PASS", name, width, f"{tag}{about}")
                tally["skipped" if skipped else "passed"] += 1
                continue
            worst = max(SEVERITIES.index(finding.severity) for _, finding in hits)
            if rule.check == "model":
                status = "CHECK"
            elif worst >= threshold:
                status = "FAIL"
            else:
                status = "WARN" if SEVERITIES[worst] == "warn" else "INFO"
            for location, finding in hits:
                where = f"{location}:{finding.line}:{finding.column}"
                out.row(status, name, width, f"{tag}{where}  {finding.excerpt!r}  {finding.fix}")
            outcome = {"FAIL": "failed", "WARN": "warnings", "INFO": "info", "CHECK": "to check"}
            tally[outcome[status]] += 1
            if status == "FAIL":
                failed.append(rule.id)
        if pack.rules:
            out.line()
        if tally["warnings"] == 1:
            tally = {("warning" if kind == "warnings" else kind): n for kind, n in tally.items()}
        return (tally if pack.rules else {}), failed

    def _no_key(self, provider: str, settings: Settings) -> str:
        """Say what to set when a provider has no API key."""
        return (
            f"no API key for {provider}: {self._missing(provider, settings)},"
            f" or point [providers.{provider}] api_key_env at the variable that holds it"
        )

    def _transport(self) -> Transport:
        """The transport ``run`` sends with: the injected one, or franca's HTTP client."""
        if self.transport is not None:
            return self.transport
        try:
            from franca.transports.httpx import HttpxTransport
        except ImportError:
            msg = "running a model needs an HTTP client: install validia[http]"
            raise CliError(msg) from None
        return HttpxTransport()

    def _record(
        self,
        output: Path,
        name: str,
        started: float,
        results: Sequence[TrialResult],
        summary: RunSummary,
        about: Mapping[str, Any],
    ) -> Path:
        """Write a run's trials and summary to a folder of its own, and return it."""
        base = self.cwd / output
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started))
        folder = base / f"{stamp}-{name}"
        number = 1
        while folder.exists():
            number += 1
            folder = base / f"{stamp}-{name}-{number}"
        folder.mkdir(parents=True)
        lines = "".join(result.to_json() + "\n" for result in results)
        (folder / "trials.jsonl").write_text(lines, encoding="utf-8")
        data = {**about, "started": stamp, **summary.to_dict()}
        (folder / "summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return folder
