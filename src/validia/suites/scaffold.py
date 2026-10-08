"""The files ``validia init`` writes into a project.

They ship inside the package under ``suites/examples/``, so what ``init`` writes is
exactly what is checked in here and covered by the tests. There is one example
suite per answer type; it is written to a folder the user names, and the only
thing that changes with the name is the run command its header comment shows.
"""

import re
from importlib import resources
from pathlib import Path, PurePosixPath

from .suite import AnswerType

__all__ = [
    "EVALS",
    "EXAMPLES",
    "SETTINGS",
    "folder_problem",
    "is_suite_name",
    "name_problem",
    "read",
    "render_suite",
    "suite_files",
]

SETTINGS = "validia.toml"
"""The settings file, with every setting at its default."""

EVALS = "evals"
"""The default folder suites live in, one sub-folder per suite."""

EXAMPLES: dict[AnswerType, str] = {
    "label": "ticket-triage",
    "json": "ticket-fields",
    "text": "help-answers",
    "tool": "support-tools",
}
"""The example suite for each answer type, by its default name."""

SUITE_PATH = "$suite_path"
"""The token in a template's header comment that becomes the suite's own path."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_suite_name(name: str) -> bool:
    """Report whether a name works as a suite folder on every platform.

    Args:
        name: The proposed name.

    Returns:
        True for letters, digits, ``.``, ``_`` and ``-``, starting with a
        letter or digit; no separators, so it is always one folder.
    """
    return _NAME.fullmatch(name) is not None


def name_problem(text: str) -> str | None:
    """Explain why a name would not make one portable folder or id, if it would not.

    Args:
        text: The proposed name.

    Returns:
        The problem, or ``None``.
    """
    if is_suite_name(text):
        return None
    return "use letters, digits, '.', '_' and '-', with no slashes"


def folder_problem(text: str) -> str | None:
    """Explain why a suites folder would land outside the project, if it would.

    Args:
        text: The proposed folder.

    Returns:
        The problem, or ``None``.
    """
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return "use a folder inside the project, as in evals"
    return None


def suite_files(answer: AnswerType) -> tuple[str, ...]:
    """List an example suite's files, ``suite.toml`` first.

    Args:
        answer: The answer type.

    Returns:
        File names, relative to the suite's folder.
    """
    extra = ("tools.json",) if answer == "tool" else ()
    return ("suite.toml", "prompt.md", *extra)


def read(name: str) -> str:
    """Return a template's raw contents.

    Args:
        name: Its path under ``suites/examples/``, as in ``"label/suite.toml"``.

    Returns:
        The file's text.
    """
    template = resources.files(__package__).joinpath("examples", *name.split("/"))
    return template.read_text(encoding="utf-8")


def render_suite(answer: AnswerType, name: str, suite_path: PurePosixPath) -> str:
    """Return one example-suite file, filled in for where it is being written.

    A plain replace rather than ``string.Template``: example text is free to
    contain a ``$``, as in a price.

    Args:
        answer: The answer type.
        name: One of :func:`suite_files`.
        suite_path: Where ``suite.toml`` lands, as the user would type it.

    Returns:
        The file's text.
    """
    return read(f"{answer}/{name}").replace(SUITE_PATH, suite_path.as_posix())
