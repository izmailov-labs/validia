"""The config command: every setting, and where its value came from."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from ..runs.access import (
    KNOWN_PROVIDERS,
)
from .common import CommandBase, Commands, _render
from .settings import KEYS, PROVIDERS


class ConfigCommand(CommandBase):
    """The ``config`` command: every resolved setting, one setting's origin, or the search."""

    def _config_parsers(self, commands: Commands, common: argparse.ArgumentParser) -> None:
        """Add the commands of this family: config."""
        config = commands.add_parser(
            "config",
            parents=[common],
            help="show resolved settings and where each one came from",
            description="Show resolved settings and the flag, variable or file each came from.",
        )
        config.add_argument("key", nargs="?", help="explain one setting, as in run.reps")
        config.add_argument(
            "--discovery",
            action="store_true",
            help="show every place settings files were searched for",
        )
        config.add_argument("--json", action="store_true", help="print values as JSON")
        config.add_argument(
            "--keys",
            action="store_true",
            help="show which providers have an API key, and where it comes from (never the key)",
        )
        config.add_argument(
            "--suite",
            type=Path,
            metavar="SUITE",
            help="show settings as this suite sees them, with its folder's validia.toml on top",
        )
        config.set_defaults(handler=self._config)

    def _config(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Show resolved settings, one setting's provenance, or the search."""
        config, settings = self._settings(args, argv, suite=args.suite)
        if args.keys:
            keys = self._keys()
            providers = list(dict.fromkeys([*KNOWN_PROVIDERS, *settings.providers]))
            width = max(len(provider) for provider in providers)
            for provider in providers:
                source = self._access(provider, settings, keys)
                found = (
                    f"key from {source}"
                    if source
                    else f"- no key: {self._missing(provider, settings)}"
                )
                print(f"{provider:<{width}}  {found}")
        elif args.discovery:
            print(config.discovery_report())
        elif args.key:
            print(config.explain(args.key))
        elif args.json:
            print(json.dumps(config.dump(), indent=2, default=str))
        else:
            access = sorted(key for key in config.dump() if key.startswith(f"{PROVIDERS}."))
            print(_render(config, (*KEYS, *access), self.cwd))
        return 0
