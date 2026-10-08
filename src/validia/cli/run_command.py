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
from ._loop import run_bounded
from .common import CliError, CommandBase, Commands, _count, _render
from .settings import SETTINGS_FILE

RUN_KEYS = (
    "model",
    "run.reps",
    "run.concurrency",
    "run.retries",
    "run.output",
    "run.fail_under",
)


"""The settings ``validia run`` reads, in the order ``--dry-run`` prints them."""


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


def _report(name: str, model: str, summary: RunSummary) -> None:
    """Print a run's summary: the pass rate first, then what explains it."""
    low, high = summary.interval
    rate = summary.rate
    shown = "no replies to grade" if rate is None else f"{rate:.1%}"
    print(
        f"{name} on {model}: {summary.passed} of {summary.graded} passed, {shown}"
        + (f" (95% interval {low:.1%}-{high:.1%})" if rate is not None else "")
    )
    if summary.served_models:
        print(f"  served by {', '.join(summary.served_models)}")
    if len(summary.groups) > 1:
        groups = ", ".join(f"{group} {p}/{g}" for group, (p, g) in summary.groups.items())
        print(f"  by group: {groups}")
    failing = [case for case in summary.cases if case.passed < case.graded]
    if failing:
        print("  failing:")
        width = max(len(case.case) for case in failing)
        for case in failing:
            print(f"    {case.case:<{width}}  {case.passed} of {case.graded}  {case.reason}")
    if summary.errors:
        kinds = ", ".join(f"{kind} {count}" for kind, count in summary.errors.items())
        print(f"  errors: {sum(summary.errors.values())} ({kinds})")
    cached = f" ({summary.cache_read_tokens:,} cached)" if summary.cache_read_tokens else ""
    print(f"  tokens: {summary.input_tokens:,} in{cached}, {summary.output_tokens:,} out")
    print(f"  latency: p50 {summary.latency_p50_ms:,.0f} ms, p95 {summary.latency_p95_ms:,.0f} ms")


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
            help="resolve settings, print what would run, and stop",
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
        suite = self._suite(args.suite)
        config, settings = self._settings(args, argv, flags, suite=args.suite)
        if settings.model is None:
            msg = f"no model to evaluate: pass --model, or set `model` in {SETTINGS_FILE}"
            raise CliError(msg)
        spec = parse_model(settings.model)
        keys = self._keys()
        source = self._access(spec.provider, settings, keys)
        if args.dry_run:
            groups = ", ".join(f"{tag} {count}" for tag, count in suite.groups().items())
            print(f"suite = {self._shown(suite.path)!r}")
            print(f"prompt = {self._shown(suite.prompt)!r}")
            print(f"cases = {len(suite.cases)}  ({groups})")
            print(f"grade = {suite.summary()}")
            print(_render(config, RUN_KEYS, self.cwd))
            print(f"access = {spec.provider}, key from {source or 'nowhere'}")
        if source is None:
            msg = (
                f"no API key for {spec.provider}: {self._missing(spec.provider, settings)},"
                f" or point [providers.{spec.provider}] api_key_env at the variable that holds it"
            )
            raise CliError(msg)
        if args.dry_run:
            return 0
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
        finished = [0]

        def progress(_: TrialResult) -> None:
            finished[0] += 1
            if sys.stderr.isatty():
                print(f"\r  {finished[0]}/{len(plan)}", end="", file=sys.stderr, flush=True)

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
        if sys.stderr.isatty():
            print(file=sys.stderr)
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
        _report(name, f"{spec.provider}:{spec.name}", summary)
        print(f"  written to {self._shown(folder)}/")
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
