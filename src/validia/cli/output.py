"""How ``validia run`` shows its cases: in colour through rich when it is installed, plain otherwise.

rich is the ``validia[rich]`` extra, not a dependency, so a library user who only
imports validia never pulls a terminal UI in. The lines are the same either way --
one per case, status first, as a test runner prints them -- and rich only adds colour
and a progress bar. It adds neither when output is not a terminal: rich probes the
stream itself, so a pipe, a file or a CI log reads exactly like the plain output.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

STYLES = {
    "PASS": "bold green",
    "FAIL": "bold red",
    "ERROR": "bold yellow",
    "WARN": "yellow",
    "INFO": "blue",
    "SKIP": "dim",
    "CHECK": "magenta",
    "ready": "cyan",
}
"""The colour of each status."""

QUIET = frozenset({"PASS", "SKIP", "ready"})
"""Statuses whose detail describes rather than explains, so it is dimmed."""

Part = str | tuple[str, str]
"""A piece of a line: plain text, or text and the rich style to show it in."""


def _console() -> "Console | None":
    """A console on stdout, or ``None`` when the ``rich`` extra is not installed."""
    try:
        from rich.console import Console
    except ImportError:
        return None
    # soft_wrap: a long reason stays one line, as it is in the plain output.
    return Console(soft_wrap=True, highlight=False)


class Output:
    """Where ``validia run`` writes its lines.

    Args:
        console: The rich console to write through; by default one on stdout, or
            none -- plain ``print`` -- when rich is not installed.
    """

    def __init__(self, console: "Console | None" = None) -> None:
        """Pick the console, once per command, so tests' captured stdout is the one used."""
        self._console = console if console is not None else _console()

    def line(self, *parts: Part) -> None:
        """Print one line, styled where rich can show it."""
        if self._console is None:
            print("".join(part if isinstance(part, str) else part[0] for part in parts))
            return
        from rich.text import Text

        self._console.print(Text.assemble(*parts))

    def row(self, status: str, name: str, width: int, detail: str = "") -> None:
        """Print one test: its status, its name, then why it failed or what it checks."""
        if not detail:
            self.line("  ", (f"{status:<5}", STYLES.get(status, "")), "  ", name)
            return
        style = "dim" if status in QUIET else ""
        self.line(
            "  ",
            (f"{status:<5}", STYLES.get(status, "")),
            "  ",
            f"{name:<{width}}  ",
            (detail, style),
        )

    @contextmanager
    def progress(self, total: int, label: str) -> Iterator[Callable[[], None]]:
        """Show a progress bar under the lines while trials run, on a terminal only.

        Args:
            total: How many trials will finish.
            label: What is running, shown beside the bar.

        Yields:
            A function to call as each trial finishes.
        """
        if self._console is None or not self._console.is_terminal:
            yield lambda: None
            return
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        columns = (
            TextColumn("{task.description}", markup=False),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )
        with Progress(*columns, console=self._console, transient=True) as bar:
            task = bar.add_task(label, total=total)
            yield lambda: bar.advance(task)
