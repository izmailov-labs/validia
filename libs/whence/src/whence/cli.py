"""The ``whence`` command: explain, dump, discovery.

One discipline, learned from someone else's outage: **data goes to stdout,
diagnostics go to stderr, and the library itself never prints at all.** Node's
dotenv added a single ``console.log`` on stdout and broke a JSON-RPC transport
in the wild, because a library that writes to stdout is a library that corrupts
whatever pipeline it is embedded in.
"""

import argparse
import json
import sys
from collections.abc import Sequence

from .config import Config
from .errors import WhenceError

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="whence",
        description="Inspect layered configuration and where each value came from.",
    )
    parser.add_argument("app", help="application name, e.g. myapp")
    parser.add_argument(
        "command",
        choices=("explain", "dump", "discovery"),
        help="explain one key, dump every value, or show how files were searched for",
    )
    parser.add_argument("key", nargs="?", help="dotted key, for `explain`")
    parser.add_argument(
        "-p",
        "--profile",
        action="append",
        default=None,
        dest="profiles",
        help="activate a profile; repeat for several, last wins",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON, for `dump`")
    parser.add_argument(
        "--reveal",
        action="store_true",
        help="do not redact secrets; pass this only deliberately",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        A process exit code.
    """
    args = _parser().parse_args(argv)
    try:
        config = Config.load(args.app, profiles=args.profiles)
        if args.command == "discovery":
            print(config.discovery_report())
        elif args.command == "dump":
            data = config.dump(reveal=args.reveal)
            if args.json:
                print(json.dumps(data, indent=2, default=str))
            else:
                print("\n".join(f"{key} = {value!r}" for key, value in data.items()))
        else:
            if not args.key:
                print("explain needs a key, e.g. `whence myapp explain db.host`", file=sys.stderr)
                return 2
            print(config.explain(args.key))
    except WhenceError as exc:
        print(f"whence: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
