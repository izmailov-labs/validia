"""The ``validia`` command.

Data goes to stdout and diagnostics to stderr; the library itself never prints.
Every setting a command uses resolves through :mod:`validia.cli.settings`, so
``validia config <key>`` can say which flag, variable or file set it, and every
question goes through :mod:`validia.cli.interview`.

    validia init [DIR]                write validia.toml and an example suite
    validia create                    create a suite: its prompt, grader and cases
    validia template                  print the prompt template create builds from
    validia add SUITE                 add a test case, asking for each field
    validia check SUITE CASE          grade a reply against one case, no model needed
    validia config [KEY]              show resolved settings and their origins
    validia lint PROMPT_OR_SUITE...   check prompts against the rules for a model
    validia rules list|test|versions|guidance|explain
                                      show, prove and compare rules, read a model's
                                      prompting instructions, or explain one rule
    validia rules extend|replace|disable|fallback|new
                                      write the project's own rule files in rules/
    validia run SUITE                 run a suite against a model and grade every reply
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from whence import WhenceError

from .. import __version__
from ..prompts.building import BuildError
from ..prompts.template import (
    TemplateError,
)
from ..rules import (
    RuleError,
)
from ..runs.access import (
    AccessError,
)
from ..suites.api import (
    SpecError,
)
from ..suites.suite import (
    SuiteError,
)
from .common import CliError
from .config_command import ConfigCommand
from .interview import (
    CancelledByUserError,
)
from .rule_commands import RuleCommands
from .run_command import RunCommand
from .settings import SETTINGS_FILE
from .suite_commands import SuiteCommands

__all__ = ["Cli", "CliError", "main"]


Handler = Callable[[argparse.Namespace, Sequence[str]], int]


def _key_value(text: str) -> str:
    """Validate a ``--set`` argument, so a malformed one is a usage error."""
    key, sep, _ = text.partition("=")
    if not sep or not key.strip():
        msg = f"expected KEY=VALUE, as in run.reps=3, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return text


class Cli(SuiteCommands, ConfigCommand, RuleCommands, RunCommand):
    """The ``validia`` command-line interface.

    Every input it reads is injectable, so a test can run it hermetically and an
    application can embed it without touching process state.

    Args:
        cwd: The directory to work in: where settings are searched for, where
            relative paths resolve, and where ``init`` writes. Defaults to the
            process's working directory.
        environ: The environment settings are read from; defaults to
            ``os.environ``.
        discovery: Where settings files are searched for.
        interactive: Whether commands may ask questions. Defaults to whether
            standard input is a terminal, so a script or CI job never blocks
            on a prompt nobody will answer.
        transport: How ``run`` reaches a model: a scripted transport in a test,
            franca's pooled HTTP client (``validia[http]``) when omitted.
        clock: Where ``run`` measures and waits; ``AsyncioClock`` when omitted.
    """

    def main(self, argv: Sequence[str] | None = None) -> int:
        """Parse arguments, run one command, and return its exit code.

        Args:
            argv: Arguments, defaulting to ``sys.argv[1:]``.

        Returns:
            ``0`` on success and ``1`` when the command failed. A usage error
            exits with ``2`` through :class:`SystemExit`, as argparse does.
        """
        arguments = list(sys.argv[1:] if argv is None else argv)
        args = self.parser().parse_args(arguments)
        handler: Handler = args.handler
        try:
            return handler(args, arguments)
        except (
            AccessError,
            BuildError,
            CancelledByUserError,
            CliError,
            SpecError,
            SuiteError,
            TemplateError,
            RuleError,
            WhenceError,
            OSError,
        ) as exc:
            problems = exc.problems if isinstance(exc, SpecError | BuildError) else [str(exc)]
            if len(problems) == 1:
                print(f"validia: {problems[0]}", file=sys.stderr)
            else:
                lines = "".join(f"\n  {problem}" for problem in problems)
                print(f"validia: {len(problems)} problems:{lines}", file=sys.stderr)
            return 1

    def parser(self) -> argparse.ArgumentParser:
        """Build the argument parser.

        Returns:
            The parser, with one subcommand per command.
        """
        parser = argparse.ArgumentParser(
            prog="validia",
            description="Evaluate prompts, tools and agents.",
        )
        parser.add_argument("--version", action="version", version=f"validia {__version__}")
        commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

        # Settings options sit on each command rather than on `validia` itself,
        # so they can follow the command: `validia run suite.toml --set run.reps=3`.
        common = argparse.ArgumentParser(add_help=False)
        options = common.add_argument_group("settings")
        options.add_argument(
            "-c",
            "--config",
            type=Path,
            metavar="FILE",
            help=f"read settings from FILE instead of searching for {SETTINGS_FILE}",
        )
        options.add_argument(
            "-p",
            "--profile",
            action="append",
            dest="profiles",
            metavar="NAME",
            help="activate a profile (validia.NAME.toml); repeat for several, last wins",
        )
        options.add_argument(
            "--set",
            action="append",
            type=_key_value,
            metavar="KEY=VALUE",
            help="override one setting, as in --set run.reps=3; repeatable",
        )

        self._suites_parsers(commands, common)
        self._config_parsers(commands, common)
        self._rules_parsers(commands, common)
        self._run_parsers(commands, common)
        return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``validia`` command; the console-script entry point.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    return Cli().main(argv)
