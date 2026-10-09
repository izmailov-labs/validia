"""The suite commands: init, create, template, add and check."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from ..prompts.template import (
    PROJECT_TEMPLATE,
    PromptTemplate,
    default_template,
    default_text,
    load_template,
)
from ..suites import scaffold
from ..suites.api import (
    CaseSpec,
    SuiteSpec,
    add_case,
    check_reply,
    create_suite,
    find_case,
)
from ..suites.authoring import case_toml
from ..suites.expect import Reply, ToolCall, Verdict
from ..suites.suite import (
    ANSWER_TYPES,
    AnswerType,
    Grade,
    Tool,
    load_tools,
)
from .common import CliError, CommandBase, Commands
from .interview import (
    ANSWER_OPTIONS,
    folder_problem,
    name_problem,
    object_problem,
)
from .settings import SETTINGS_FILE

ANSWER_HELP = ", ".join(f"{answer} = {meaning}" for answer, meaning in ANSWER_OPTIONS)


"""What each answer type means, for ``--help``."""


PROMPT_SOURCES = (
    ("build", "answer a few questions, section by section"),
    ("write", "type it here"),
    ("file", "use an existing file; the suite points at it, so it tests the prompt you ship"),
    ("later", "start from a placeholder in prompt.md and fill it in later"),
)


"""Where ``create`` takes the prompt under test from."""


TOOL_SOURCES = (
    ("define", "describe each tool here: its name, what it does, its arguments"),
    ("file", "use an existing JSON file of tool definitions"),
)


"""Where ``create`` takes a tool suite's tools from."""


PLACEHOLDER = "Write the system prompt under test here.\n"


"""What ``prompt.md`` holds when the prompt is to be written later."""


def _evals_folder(text: str) -> Path:
    """Validate ``--evals``, so a folder outside the project is a usage error."""
    problem = folder_problem(text)
    if problem is not None:
        raise argparse.ArgumentTypeError(problem)
    return Path(text)


def _suite_name(text: str) -> str:
    """Validate ``--name``, so a bad one is a usage error."""
    problem = name_problem(text)
    if problem is not None:
        raise argparse.ArgumentTypeError(f"{text!r}: {problem}")
    return text


def _json_object(text: str) -> dict[str, Any]:
    """Validate ``--args``, so a malformed one is a usage error."""
    problem = object_problem(text)
    if problem is not None:
        raise argparse.ArgumentTypeError(problem)
    value: dict[str, Any] = json.loads(text)
    return value


def _verdict(verdict: Verdict) -> str:
    """Render a verdict as ``check`` and ``add`` print it."""
    return "pass" if verdict.passed else f"fail: {verdict.reason}"


class SuiteCommands(CommandBase):
    """The commands that place, build and grade suites: ``init``, ``create``, ``template``, ``add`` and ``check``."""

    def _suites_parsers(self, commands: Commands, common: argparse.ArgumentParser) -> None:
        """Add the commands of this family: init, create, template, add and check."""
        # `init` and `create` both place a suite: a folder for suites, an answer
        # type and a name, each asked unless a flag says it.
        placing = argparse.ArgumentParser(add_help=False)
        placing.add_argument(
            "--evals",
            type=_evals_folder,
            metavar="DIR",
            help=f"folder for eval suites, inside the project (default: {scaffold.EVALS})",
        )
        placing.add_argument(
            "--answer",
            choices=ANSWER_TYPES,
            help=f"what the prompt answers with: {ANSWER_HELP}",
        )
        placing.add_argument("--name", type=_suite_name, help="the suite's name")

        init = commands.add_parser(
            "init",
            parents=[placing],
            help=f"write a starter {SETTINGS_FILE} and an example suite",
            description=(
                f"Write {SETTINGS_FILE} with every setting at its default, and an example"
                " suite to copy from: a prompt, balanced cases, and a grader. Asks for the"
                " folder, the answer type and the name unless --evals, --answer and --name"
                " say."
            ),
        )
        init.add_argument(
            "directory",
            nargs="?",
            type=Path,
            default=Path(),
            help="the project to write into (default: the current directory)",
        )
        init.add_argument("-f", "--force", action="store_true", help="overwrite existing files")
        init.add_argument(
            "--no-example",
            dest="example",
            action="store_false",
            help=f"write only {SETTINGS_FILE}",
        )
        init.set_defaults(handler=self._init)

        create = commands.add_parser(
            "create",
            parents=[placing],
            help="create a suite from questions: its prompt, grader and cases",
            description=(
                "Create a suite of your own, one question at a time: where it goes, what"
                " the prompt answers with, the labels, keys or tools that grading needs,"
                " the prompt itself, and the cases. Nothing is written until the last"
                " answer, and only if the suite loads."
            ),
        )
        create.set_defaults(handler=self._create)

        template = commands.add_parser(
            "template",
            help=f"print the prompt template `create` builds from, to save as {PROJECT_TEMPLATE}",
            description=(
                "Print the built-in prompt template: the questions `create` asks to build"
                " a prompt, the core checks it runs on each answer, and the closing"
                f" instructions it offers. Save it as {PROJECT_TEMPLATE} in the project"
                " to change any of them."
            ),
        )
        template.set_defaults(handler=self._template)

        add = commands.add_parser(
            "add",
            help="add a test case to a suite, asking for each field",
            description=(
                "Add a case to a suite. Asks for its id, input and tags, then for the"
                " expected answer in the shape the suite's answer type calls for: a"
                " label, JSON fields, text checks or a tool call. Appends it to the"
                " suite file, then lets you try replies against it."
            ),
        )
        add.add_argument("suite", type=Path, help="the suite file to add to")
        add.set_defaults(handler=self._add)

        check = commands.add_parser(
            "check",
            help="grade a reply against one case, without calling a model",
            description=(
                "Grade a reply you supply against one case: test a regex, a contains"
                " list, JSON fields or a tool call before any model runs. Exits 0 when"
                " the reply passes and 1 when it fails."
            ),
        )
        check.add_argument("suite", type=Path, help="the suite file")
        check.add_argument("case", help="the id of the case to check against")
        check.add_argument(
            "--reply",
            help="the reply text (asked in a terminal, otherwise read from stdin, when omitted)",
        )
        check.add_argument("--tool", help="a tool the reply called, for tool suites")
        check.add_argument(
            "--args",
            type=_json_object,
            default={},
            metavar="JSON",
            help="that call's arguments, as a JSON object",
        )
        check.set_defaults(handler=self._check)

    def _example(self, args: argparse.Namespace, root: Path) -> tuple[AnswerType, PurePosixPath]:
        """Decide which example to write and where: flags first, then questions, then defaults.

        A name whose folder already holds a suite is asked again, so nobody answers
        every question only to be refused at the end. ``--force`` allows it.
        """
        evals: str = scaffold.EVALS if args.evals is None else args.evals.as_posix()
        answer: AnswerType = "label" if args.answer is None else args.answer
        if self.interactive and args.evals is None:
            evals = self.ask.ask("Folder for eval suites", evals, folder_problem)
        if self.interactive and args.answer is None:
            answer = self.ask.answer_type()

        def free(name: str) -> str | None:
            problem = name_problem(name)
            folder = root / evals / name
            if problem is None and not args.force and self._has_suite(folder, answer):
                problem = f"{self._shown(folder)} already has a suite; choose another name"
            return problem

        name: str = scaffold.EXAMPLES[answer] if args.name is None else args.name
        if self.interactive and args.name is None:
            name = self.ask.ask("Name of the example suite", name, free)
        return answer, PurePosixPath(evals) / name

    @staticmethod
    def _has_suite(folder: Path, answer: AnswerType) -> bool:
        """Report whether writing the example into ``folder`` would overwrite anything."""
        return any((folder / name).exists() for name in scaffold.suite_files(answer))

    def _init(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Write the settings file and, unless declined, the example suite.

        An existing settings file is kept: it is the project's configuration, and
        running ``init`` again is how a second suite gets added. Suite files are
        never overwritten without ``--force``, and when one would be, nothing at
        all is written, so a refused ``init`` leaves no half-written project.
        """
        root = self.cwd / args.directory
        settings = root / SETTINGS_FILE
        keep = settings.exists() and not args.force
        files: dict[Path, str] = {} if keep else {settings: scaffold.read(scaffold.SETTINGS)}
        suite_path: Path | None = None
        if args.example:
            answer, folder = self._example(args, root)
            names = scaffold.suite_files(answer)
            suite_path = root / folder / names[0]
            shown = PurePosixPath(self._shown(suite_path))
            for name in names:
                files[root / folder / name] = scaffold.render_suite(answer, name, shown)
        existing = [self._shown(path) for path in files if path.exists()]
        if existing and not args.force:
            msg = f"already exists: {', '.join(existing)}; pass --force to overwrite"
            raise CliError(msg)
        if keep:
            print(f"kept {self._shown(settings)} (already exists)")
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {self._shown(path)}")
        if suite_path is not None:
            print(
                f"next: validia run {self._shown(suite_path)} --dry-run",
                file=sys.stderr,
            )
        return 0

    def _create(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Ask for a suite, then create it through :func:`validia.suites.api.create_suite`."""
        self._interactive("create", "start from an example with `validia init`")
        ask = self.ask
        evals = args.evals.as_posix() if args.evals is not None else None
        if evals is None:
            evals = ask.ask("Folder for eval suites", scaffold.EVALS, folder_problem)

        def free(name: str) -> str | None:
            if not name:
                return "a suite needs a name"
            folder = self.cwd / evals / name
            if folder.exists():
                return f"{self._shown(folder)} already exists; choose another name"
            return name_problem(name)

        if args.name is not None and (taken := free(args.name)) is not None:
            raise CliError(taken)
        name: str = args.name if args.name is not None else ask.ask("Suite name", valid=free)
        answer: AnswerType = args.answer if args.answer is not None else ask.answer_type()
        grade = Grade(
            answer,
            labels=ask.labels() if answer == "label" else (),
            required=ask.required() if answer == "json" else (),
        )

        tools: tuple[Tool, ...] = ()
        tools_file = ""
        if answer == "tool":
            if ask.choose("The tools the model is offered", TOOL_SOURCES, "define") == "define":
                tools = ask.tools()
            else:
                path = ask.tools_file(self.cwd)
                tools, tools_file = load_tools(path), self._shown(path)

        prompt, prompt_file = "", ""
        source = ask.choose("The prompt under test", PROMPT_SOURCES, "build")
        if source == "build":
            prompt = ask.build_prompt(self._prompt_template(), grade, tools)
        elif source == "write":
            prompt = ask.block("Write the prompt")
        elif source == "file":
            prompt_file = ask.ask(
                "Path to the prompt file",
                valid=lambda text: None if (self.cwd / text).is_file() else f"no such file: {text}",
            )
        else:
            prompt = PLACEHOLDER
        description = ask.ask("Describe the suite in one line (empty to skip)")

        print(
            "Now the cases. Cover both sides of every decision the prompt makes.",
            file=sys.stderr,
        )
        cases: list[CaseSpec] = []
        while True:
            taken_ids = {case.id for case in cases}
            groups = list(dict.fromkeys(case.tags[0] for case in cases if case.tags))
            cases.append(ask.case(grade, tools, taken_ids, groups))
            if not ask.confirm("Add another case?"):
                break

        spec = SuiteSpec(
            name=name,
            answer=answer,
            cases=tuple(cases),
            folder=evals,
            labels=grade.labels,
            required=grade.required,
            prompt=prompt,
            prompt_file=prompt_file,
            tools=() if tools_file else tools,
            tools_file=tools_file,
            description=description,
        )
        suite = create_suite(spec, self.cwd)
        written = (
            ["suite.toml"] + ["prompt.md"] * bool(spec.prompt) + ["tools.json"] * bool(spec.tools)
        )
        for file in written:
            print(f"wrote {self._shown(suite.path.parent / file)}")
        print(
            f"next: validia run {self._shown(suite.path)} --dry-run",
            file=sys.stderr,
        )
        return 0

    def _prompt_template(self) -> PromptTemplate:
        """Use the project's prompt.toml when there is one, else the built-in template."""
        path = self.cwd / PROJECT_TEMPLATE
        return load_template(path) if path.is_file() else default_template()

    def _template(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Print the built-in prompt template."""
        print(default_text(), end="")
        return 0

    def _add(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Ask for a case, add it through :func:`validia.suites.api.add_case`, then try replies."""
        self._interactive("add", "edit the suite file by hand")
        suite = self._suite(args.suite)
        print(f"Adding a case to {self._shown(suite.path)}: {suite.summary()}", file=sys.stderr)
        taken = {case.id for case in suite.cases}
        spec = self.ask.case(suite.grade, suite.tools, taken, list(suite.groups()))
        suite = add_case(suite.path, spec)
        print(f"added {spec.id} to {self._shown(suite.path)}")
        print(case_toml(spec.id, spec.input, spec.expected, spec.tags), end="")
        print("Try replies against it to test its checks.", file=sys.stderr)
        while (
            reply := self.ask.reply(suite.grade, suite.tools, finish="empty to finish")
        ) is not None:
            print(_verdict(check_reply(suite, spec.id, reply)))
        return 0

    def _check(self, args: argparse.Namespace, argv: Sequence[str]) -> int:
        """Grade one reply through :func:`validia.suites.api.check_reply`; exit 0 to pass, 1 to fail."""
        suite = self._suite(args.suite)
        find_case(suite, args.case)  # an unknown case fails before anything is asked
        if args.reply is not None or args.tool is not None:
            calls = (ToolCall(args.tool, args.args),) if args.tool else ()
            reply: Reply | None = Reply(text=args.reply or "", tool_calls=calls)
        elif self.interactive:
            reply = self.ask.reply(suite.grade, suite.tools, finish="empty to stop")
        else:
            reply = Reply(text=sys.stdin.read())
        if reply is None:
            msg = "no reply to check"
            raise CliError(msg)
        verdict = check_reply(suite, args.case, reply)
        print(_verdict(verdict))
        return 0 if verdict.passed else 1
