"""Command-line overrides: the layer an operator reaches for last.

Deliberately narrow. whence is not an argument parser, and pretending otherwise
means owning a second, worse one -- so this reads only ``--set key=value`` and
leaves every other argument alone. A project with a real CLI should parse its
own arguments and pass them to ``Config.load(overrides=...)``.
"""

import sys
from collections.abc import Sequence

from ..errors import ConfigError
from ..keys import KeyPath, canonical
from ..origin import Origin, Tracked
from ..tree import Layer

__all__ = ["ArgvSource"]

FLAG = "--set"
"""The only flag this source recognises."""


class ArgvSource:
    """Reads ``--set key=value`` pairs out of an argument list.

    Args:
        argv: The arguments to scan; defaults to ``sys.argv[1:]``. Passing an
            explicit list is what makes a test hermetic.
        name: The layer name.
        flag: The flag to recognise, if ``--set`` collides with an existing one.
    """

    def __init__(
        self,
        argv: Sequence[str] | None = None,
        *,
        name: str = "cli",
        flag: str = FLAG,
    ) -> None:
        """Build a command-line source."""
        self.name = name
        self.flag = flag
        self.argv = list(sys.argv[1:] if argv is None else argv)

    def _pairs(self) -> list[tuple[str, str, int]]:
        """Extract ``(key, value, argv index)`` triples, both flag spellings."""
        out: list[tuple[str, str, int]] = []
        i = 0
        while i < len(self.argv):
            arg = self.argv[i]
            if arg == self.flag:
                if i + 1 >= len(self.argv):
                    msg = f"{self.flag} needs an argument, as in `{self.flag} db.host=localhost`"
                    raise ConfigError(msg)
                body, index = self.argv[i + 1], i + 1
                i += 2
            elif arg.startswith(f"{self.flag}="):
                body, index = arg[len(self.flag) + 1 :], i
                i += 1
            else:
                i += 1
                continue
            key, sep, value = body.partition("=")
            if not sep or not key.strip():
                msg = f"{self.flag} expects key=value, as in `{self.flag} db.host=localhost`"
                raise ConfigError(msg)
            out.append((key.strip(), value, index))
        return out

    def load(self) -> Layer:
        """Collect every ``--set`` pair.

        Returns:
            The layer, with ``found=False`` when no pair was given.

        Raises:
            ConfigError: If a pair is malformed.
        """
        entries: dict[KeyPath, Tracked] = {}
        for key, value, index in self._pairs():
            entries[canonical(key)] = Tracked(
                value, Origin(self.name, f"{self.flag} {key}", line=index + 1)
            )
        return Layer(self.name, entries, found=bool(entries))
