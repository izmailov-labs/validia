"""What every command family shares: settings, files, keys, and the error they raise."""

import argparse
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from franca.core.clock import Clock
from franca.core.transport import Transport
from whence import Config, Discovery

from ..rules import PROJECT_RULES, RulePack, Scope, Target, default_rules, load_rules
from ..runs.access import (
    KeyEnvironment,
    key_source,
    key_variables,
    parse_model,
)
from ..suites.suite import (
    Suite,
    load_suite,
)
from .interview import (
    Interview,
)
from .settings import DISCOVERY, PROVIDERS, Settings, for_suite, load

type Commands = argparse._SubParsersAction[argparse.ArgumentParser]
"""The subparsers a command family adds its commands to."""


class CliError(Exception):
    """A command failed in a way the user can fix; the message says how."""


def _render(config: Config, keys: Iterable[str], base: Path | None = None) -> str:
    """Format settings as aligned ``key = value  <- origin`` lines.

    Origins inside ``base`` are shown relative to it: ``evals/x/validia.toml``
    says as much as the absolute path, in a fraction of the width.
    """
    values = config.dump()
    origins = config.origins()

    def where(key: str) -> str:
        origin = origins[key]
        locator = Path(origin.locator)
        if base is not None and locator.is_relative_to(base):
            origin = replace(origin, locator=locator.relative_to(base).as_posix())
        return str(origin)

    rows = [
        (f"{key} = {values[key]!r}", f"<- {where(key)}") if key in values else (key, "- not set")
        for key in keys
    ]
    width = max((len(left) for left, _ in rows), default=0)
    return "\n".join(f"{left:<{width}}  {right}" for left, right in rows)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


class CommandBase:
    """State and helpers every command family shares: where to work, what to read, how to ask."""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        environ: Mapping[str, str] | None = None,
        discovery: Discovery = DISCOVERY,
        interactive: bool | None = None,
        transport: Transport | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Build the interface."""
        self.cwd = Path.cwd() if cwd is None else cwd
        self.environ = environ
        self.discovery = discovery
        self.transport = transport
        self.clock = clock
        if interactive is None:
            interactive = sys.stdin is not None and sys.stdin.isatty()
        self.interactive = interactive
        self.ask = Interview()

    def _settings(
        self,
        args: argparse.Namespace,
        argv: Sequence[str],
        flags: Mapping[str, Any] | None = None,
        suite: Path | None = None,
    ) -> tuple[Config, Settings]:
        """Resolve settings for one command.

        Flags become overrides, the top layer. ``--set`` pairs are read from the
        raw arguments by whence itself, so their origin names the argument's
        position on the command line. With a suite, its folder's ``validia.toml``
        outranks the project's.
        """
        discovery = self.discovery
        if suite is not None:
            folder = self.cwd / (suite if (self.cwd / suite).is_dir() else suite.parent)
            discovery = for_suite(discovery, folder, self.cwd)
        if args.config is not None:
            discovery = discovery.with_(file=self.cwd / args.config)
        overrides = {key: value for key, value in (flags or {}).items() if value is not None}
        return load(
            discovery=discovery,
            profiles=args.profiles,
            argv=argv,
            overrides=overrides,
            environ=self.environ,
            cwd=self.cwd,
        )

    def _require_files(self, paths: Iterable[Path]) -> None:
        """Check input files exist, relative to the working directory.

        Raises:
            CliError: If any is missing, naming every one at once.
        """
        missing = [path.as_posix() for path in paths if not (self.cwd / path).is_file()]
        if missing:
            msg = f"no such file: {', '.join(missing)}"
            raise CliError(msg)

    def _shown(self, path: Path) -> str:
        """Render a path relative to the working directory when it is inside it."""
        return (path.relative_to(self.cwd) if path.is_relative_to(self.cwd) else path).as_posix()

    def _suite(self, path: Path) -> Suite:
        """Load a suite named on the command line."""
        self._require_files([path])
        return load_suite(self.cwd / path)

    @staticmethod
    def _target(model: str | None) -> Target | None:
        """The model rules are resolved for, as ``(provider, model)``.

        Raises:
            AccessError: If the provider cannot be told from the name.
        """
        if model is None:
            return None
        spec = parse_model(model)
        return (spec.provider, spec.name)

    def _pack(
        self,
        settings: Settings,
        categories: Sequence[str] | None = None,
        names: Sequence[str] | None = None,
    ) -> RulePack:
        """The rules in use for the model in settings: the core, then the project's rules/.

        ``names`` picks rules by category, id or how they decide, before ``categories``
        narrows them; both are checked against every rule in use.
        """
        target = self._target(settings.model)
        pins = settings.lint.rules.pinned()
        project = self.cwd / PROJECT_RULES

        def load(kept: Sequence[str] | None) -> RulePack:
            if project.is_dir():
                return load_rules(project, target=target, pins=pins, categories=kept)
            return default_rules(target, pins=pins, categories=kept)

        pack = load(categories)
        if names is None:
            return pack
        # Names are checked against every rule, so --category cannot hide a name's category.
        every = load(None) if categories else pack
        return every.select(names).only(pack.categories)

    @staticmethod
    def _suite_texts(suite: Suite, base: Path) -> list[tuple[str, str, Scope]]:
        """A suite's texts the rules read -- its prompt, each tool's description -- named from ``base``."""

        def shown(path: Path) -> str:
            return (path.relative_to(base) if path.is_relative_to(base) else path).as_posix()

        texts: list[tuple[str, str, Scope]] = [
            (shown(suite.prompt), suite.prompt.read_text(encoding="utf-8"), "prompt")
        ]
        where = shown(suite.tools_file) if suite.tools_file else "tools"
        texts += [
            (f"{where}#{tool.name}", tool.description, "tool_description") for tool in suite.tools
        ]
        return texts

    def _interactive(self, command: str, instead: str) -> None:
        """Refuse a command that asks questions when nobody can answer them."""
        if not self.interactive:
            msg = f"`{command}` asks questions; run it in a terminal, or {instead}"
            raise CliError(msg)

    def _keys(self) -> KeyEnvironment:
        """The environment API keys are looked up in: the process's, over the project's .env.

        Keys are deliberately not settings -- franca's ``SettingsKeyProvider`` reads
        them from an environment when it calls, and this is the one it will get.
        """
        process = os.environ if self.environ is None else self.environ
        files = [path if path.is_absolute() else self.cwd / path for path in self.discovery.dotenv]
        return KeyEnvironment.load(process, files, self.cwd)

    def _access(self, provider: str, settings: Settings, keys: KeyEnvironment) -> str | None:
        """Describe where a provider's key comes from, or ``None`` when it has none."""
        source = key_source(provider, settings.providers, keys)
        if source is None:
            return None
        if source.startswith(f"{PROVIDERS}."):
            return f"{source} in settings"
        return f"{source} in {keys.where(source)}"

    def _missing(self, provider: str, settings: Settings) -> str:
        """Say exactly what to set when a provider has no key."""
        names = " or ".join(key_variables(provider, settings.providers))
        return f"set {names}, in the environment or in .env"
