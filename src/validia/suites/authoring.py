"""Writing cases into a suite file, for ``validia add``.

The standard library reads TOML but does not write it, and a suite is a file
people edit by hand -- comments, ordering and spacing are theirs. So a new case is
rendered on its own and appended as text, never round-tripped through a parser,
and the suite is loaded again afterwards: if the result does not validate, the
file is put back exactly as it was.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .suite import Grade, Suite, SuiteError, load_suite

__all__ = ["append_case", "case_toml", "suite_toml", "toml_value", "write_suite"]

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _string(text: str) -> str:
    """Render a string, as a literal one when that saves escaping a regex."""
    if "\\" in text and "'" not in text and text.isprintable():
        return f"'{text}'"
    return json.dumps(text, ensure_ascii=False)


def _key(key: str) -> str:
    """Render a key, quoted only when it has to be."""
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def toml_value(value: Any) -> str:
    """Render one value as inline TOML.

    Args:
        value: A string, number, boolean, list or mapping of those.

    Returns:
        The TOML text.

    Raises:
        ValueError: For ``None`` and anything else TOML cannot hold.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return f"[{', '.join(toml_value(item) for item in value)}]"
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        inner = ", ".join(f"{_key(str(key))} = {toml_value(item)}" for key, item in value.items())
        return f"{{ {inner} }}"
    msg = f"TOML cannot hold {value!r}; use a string such as 'unknown' instead"
    raise ValueError(msg)


def case_toml(case_id: str, text: str, expected: Any, tags: tuple[str, ...] = ()) -> str:
    """Render one case as a ``[[cases]]`` block, laid out like the examples.

    Args:
        case_id: The case's id.
        text: Its input.
        expected: Its ``expected`` value, already in the suite's shape.
        tags: Its tags, if any.

    Returns:
        The block, ending in a newline.
    """
    lines = ["[[cases]]", f"id = {toml_value(case_id)}"]
    if tags:
        lines.append(f"tags = {toml_value(list(tags))}")
    lines += [f"input = {toml_value(text)}", f"expected = {toml_value(expected)}"]
    return "\n".join(lines) + "\n"


def append_case(path: Path, block: str) -> Suite:
    """Append a case to a suite file, keeping it only if the suite still loads.

    Args:
        path: The suite file.
        block: The case, as :func:`case_toml` renders it.

    Returns:
        The suite as it now stands.

    Raises:
        SuiteError: If the suite no longer loads; the file is restored first.
    """
    before = path.read_text(encoding="utf-8")
    separator = "" if before.endswith("\n\n") else ("\n" if before.endswith("\n") else "\n\n")
    path.write_text(f"{before}{separator}{block}", encoding="utf-8")
    try:
        return load_suite(path)
    except SuiteError:
        path.write_text(before, encoding="utf-8")
        raise


def suite_toml(
    *,
    grade: Grade,
    prompt: str,
    cases: list[str],
    run: str,
    description: str = "",
    tools: str = "",
) -> str:
    """Render a new suite file, laid out like the examples ``init`` writes.

    Args:
        grade: How replies are scored.
        prompt: The prompt file, relative to the suite.
        cases: Case blocks, as :func:`case_toml` renders them.
        run: The suite's path as the user types it, for the header's run command.
        description: What the suite measures.
        tools: The tools file, relative to the suite, for a ``tool`` suite.

    Returns:
        The file's text.
    """
    lines = [
        "# A validia suite: the prompt under test, the cases to run it on, and how",
        "# to grade them. Check it without calling a model:",
        "#",
        f"#   validia run {run} --model MODEL --dry-run",
        "#",
        "# Add cases with `validia add`, test a check with `validia check`.",
        "",
    ]
    if description:
        lines.append(f"description = {toml_value(description)}")
    lines.append(f"prompt = {toml_value(prompt)}")
    if tools:
        lines.append(f"tools = {toml_value(tools)}")
    lines += ["", "[grade]", f"type = {toml_value(grade.type)}"]
    if grade.labels:
        lines.append(f"labels = {toml_value(list(grade.labels))}")
    if grade.required:
        lines.append(f"required = {toml_value(list(grade.required))}")
    return "\n".join(lines) + "\n\n" + "\n".join(cases)


def write_suite(folder: Path, files: Mapping[str, str]) -> Suite:
    """Write a new suite's files, keeping them only if the suite loads.

    Args:
        folder: The suite's folder; created when missing.
        files: File names in the folder, ``suite.toml`` among them, to their text.

    Returns:
        The suite.

    Raises:
        SuiteError: If it does not load; everything written is removed first.
    """
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for name, text in files.items():
            path = folder / name
            path.write_text(text, encoding="utf-8")
            written.append(path)
        return load_suite(folder / "suite.toml")
    except SuiteError:
        for path in written:
            path.unlink()
        if created:
            folder.rmdir()
        raise
