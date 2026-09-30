"""Where configuration comes from, as data rather than as hard-coded behaviour.

Everything about *finding* configuration lives in one frozen object: which
paths, which formats, which environment prefix, and what to fall back to. The
defaults are conventional, and every one of them is a field you can set.

The fallback chain has five steps, tried in order. Each may contribute files,
and each is recorded even when it contributes nothing -- "why is my config file
being ignored?" is the second most common configuration question after "where
did this value come from?", and it is the one every library in the field
answers with silence.

    1. an explicit ``file=``            -- highest, and missing is an error
    2. ``$MYAPP_CONFIG``                -- the operator's override
    3. each root in ``path``            -- the project
    4. the per-user config directory    -- the developer's machine
    5. ``pyproject.toml`` ``[tool.x]``  -- the repository's own default

More specific to this deployment sits higher. A hit does not stop the chain
unless ``mode="first"``.
"""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from ._platform import Platform, same_file_key, user_config_dirs
from .errors import AmbiguousConfigError, ConfigError, FormatError, MissingConfigError
from .keys import KeyPath, default_prefix, flatten, validate_prefix
from .origin import Origin, Tracked
from .sources.files import FileSource
from .tree import Layer

__all__ = ["Discovery", "DiscoveryPlan", "PyProjectSource", "Step"]

DEFAULT_FORMATS = ("toml", "yaml", "yml", "json", "properties", "xml")
"""Suffixes searched by default, in preference order."""


@dataclass(frozen=True, slots=True)
class Step:
    """One step of the discovery chain, and what it found.

    Attributes:
        index: The step number, 1 through 5.
        label: A human-readable description of where it looked.
        paths: Files it contributed, highest precedence first.
        note: Why it contributed nothing, when it did not.
    """

    index: int
    label: str
    paths: tuple[Path, ...] = ()
    note: str = ""

    @property
    def found(self) -> bool:
        """Whether this step contributed anything.

        Returns:
            True when at least one file was found.
        """
        return bool(self.paths)


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    """The outcome of running discovery: what was searched, and what was found.

    Attributes:
        steps: Every step, in order, including the ones that found nothing.
        files: Every discovered file, highest precedence first.
    """

    steps: tuple[Step, ...]
    files: tuple[tuple[Path, str | None], ...]

    def render(self) -> str:
        """Format the plan the way ``whence explain --discovery`` prints it.

        Returns:
            A multi-line report.
        """
        lines = []
        for step in self.steps:
            head = f"  {step.index} {step.label:<30}"
            if step.found:
                lines.append(f"{head} {', '.join(p.name for p in step.paths)}")
            else:
                lines.append(f"{head} - {step.note or 'not found'}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Discovery:
    """Declarative configuration discovery.

    Attributes:
        app: The application name. Every other default derives from it.
        file: An explicit configuration file. Wins outright, and must exist.
        path: Search roots, highest precedence first.
        formats: Suffixes to look for, in preference order. Restricting this is
            how a project bans a format: a stray ``app.yaml`` is then simply
            never discovered, and the plan says so.
        prefix: The environment variable prefix. ``None`` derives ``MYAPP_``.
        env_var: The variable naming an explicit config file. ``None`` derives
            ``MYAPP_CONFIG``.
        profiles_var: The variable listing active profiles. ``None`` derives
            ``MYAPP_PROFILES``.
        pyproject_table: The dotted table to read from ``pyproject.toml``.
            ``None`` derives ``tool.myapp``. Set to ``""`` to skip step 5.
        user_config: Whether to search the per-user configuration directory.
        search_parents: Walk search roots upward, for monorepos.
        boundary: Marker names that stop the upward walk.
        mode: ``"layer"`` keeps every root as its own source; ``"first"`` stops
            at the first root that matches.
        on_missing: ``"error"`` requires that discovery find at least one file.
        dotenv: ``.env`` files to read, highest precedence first.
        secrets_dir: A key-per-file secrets directory, or ``None`` to skip it.
    """

    app: str
    file: Path | None = None
    path: tuple[Path, ...] = (Path(),)
    formats: tuple[str, ...] = DEFAULT_FORMATS
    prefix: str | None = None
    env_var: str | None = None
    profiles_var: str | None = None
    pyproject_table: str | None = None
    user_config: bool = True
    search_parents: bool = False
    boundary: tuple[str, ...] = (".git", "pyproject.toml")
    mode: Literal["layer", "first"] = "layer"
    on_missing: Literal["ok", "error"] = "ok"
    dotenv: tuple[Path, ...] = (Path(".env"),)
    secrets_dir: Path | None = field(default=Path("/run/secrets"))

    def __post_init__(self) -> None:
        """Validate the prefix before anything can depend on it."""
        validate_prefix(self.env_prefix)

    @property
    def env_prefix(self) -> str:
        """The environment prefix, derived from ``app`` when unset.

        Returns:
            ``"MYAPP_"``.
        """
        return default_prefix(self.app) if self.prefix is None else self.prefix

    @property
    def config_var(self) -> str:
        """The variable naming an explicit config file.

        Returns:
            ``"MYAPP_CONFIG"``.
        """
        return self.env_var or f"{self.app.upper()}_CONFIG"

    @property
    def profiles_env(self) -> str:
        """The variable listing active profiles.

        Returns:
            ``"MYAPP_PROFILES"``.
        """
        return self.profiles_var or f"{self.app.upper()}_PROFILES"

    @property
    def table(self) -> str:
        """The ``pyproject.toml`` table to read.

        Returns:
            ``"tool.myapp"``, or ``""`` when step 5 is disabled.
        """
        return f"tool.{self.app}" if self.pyproject_table is None else self.pyproject_table

    def with_(self, **changes: object) -> "Discovery":
        """Return a copy with fields replaced.

        Args:
            **changes: Fields to override.

        Returns:
            A new discovery object.
        """
        return replace(self, **changes)  # type: ignore[arg-type]

    def roots(self, cwd: Path | None = None) -> tuple[Path, ...]:
        """Resolve the search roots, expanding the upward walk.

        With ``search_parents``, each relative root is also looked for in every
        parent directory up to and including the first one holding a
        ``boundary`` marker. In a uv workspace, where a test run may start in
        ``libs/<member>/`` while the configuration sits at the repository root,
        this is the difference between working and not.

        Args:
            cwd: The directory to start from; defaults to the process's.

        Returns:
            Roots, nearest first.
        """
        base = Path.cwd() if cwd is None else cwd
        out: list[Path] = []
        for root in self.path:
            if root.is_absolute() or not self.search_parents:
                out.append(root if root.is_absolute() else base / root)
                continue
            for parent in (base, *base.parents):
                out.append(parent / root)
                if any((parent / marker).exists() for marker in self.boundary):
                    break
        return tuple(out)

    def stems(self, profiles: Sequence[str] = ()) -> tuple[tuple[str, str | None], ...]:
        """List the file stems to look for, highest precedence first.

        Args:
            profiles: Active profiles, in increasing precedence.

        Returns:
            ``(stem, profile)`` pairs. Later profiles outrank earlier ones, and
            every profile outranks the base file.
        """
        out: list[tuple[str, str | None]] = [
            (f"{self.app}.{profile}", profile) for profile in reversed(profiles)
        ]
        out.append((self.app, None))
        return tuple(out)

    def _match(self, root: Path, stem: str) -> Path | None:
        """Find the one file in ``root`` matching ``stem``.

        Raises:
            AmbiguousConfigError: If two suffixes both match in this root.
        """
        hits: list[Path] = []
        seen: set[object] = set()
        for suffix in self.formats:
            candidate = root / f"{stem}.{suffix}"
            if not candidate.is_file():
                continue
            key = same_file_key(candidate)
            if key in seen:
                continue
            seen.add(key)
            hits.append(candidate)
        if len(hits) > 1:
            msg = (
                f"{root} holds more than one {stem} configuration file "
                f"({', '.join(p.name for p in hits)}); "
                "remove one, or narrow `formats` to say which wins"
            )
            raise AmbiguousConfigError(msg)
        return hits[0] if hits else None

    def _scan(
        self, index: int, root: Path, label: str, stems: Sequence[tuple[str, str | None]]
    ) -> tuple[Step, list[tuple[Path, str | None]]]:
        """Search one directory for every stem, and record what it found.

        Args:
            index: The step number to record.
            root: The directory to search.
            label: How the step is described in the report.
            stems: ``(stem, profile)`` pairs, highest precedence first.

        Returns:
            The step, and the files it contributed.
        """
        hits: list[tuple[Path, str | None]] = []
        for stem, profile in stems:
            found = self._match(root, stem)
            if found is not None:
                hits.append((found, profile))
        if hits:
            return Step(index, label, tuple(path for path, _ in hits)), hits
        return Step(index, label, note="no match"), []

    def plan(
        self,
        *,
        profiles: Sequence[str] = (),
        environ: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        platform: Platform | None = None,
    ) -> DiscoveryPlan:
        """Run the five-step chain and report exactly what happened.

        Args:
            profiles: Active profiles, in increasing precedence.
            environ: The environment to consult.
            cwd: The directory to start from.
            platform: Override the platform, for cross-platform tests.

        Returns:
            The plan: every step, and every file found.

        Raises:
            MissingConfigError: If an explicitly named file is absent, or
                ``on_missing="error"`` and nothing at all was found.
            AmbiguousConfigError: If one root holds two matching files.
        """
        env = environ if environ is not None else {}
        steps: list[Step] = []
        files: list[tuple[Path, str | None]] = []
        stems = self.stems(profiles)

        # 1 - an explicit file, which must exist.
        if self.file is not None:
            if not self.file.is_file():
                msg = f"configuration file {self.file} was named explicitly but does not exist"
                raise MissingConfigError(msg)
            files.append((self.file, None))
            steps.append(Step(1, "file=", (self.file,)))
        else:
            steps.append(Step(1, "file=", note="not given"))

        # 2 - the operator's environment override, which must also exist.
        named = env.get(self.config_var)
        if named:
            candidate = Path(named)
            if not candidate.is_file():
                msg = f"${self.config_var} points at {named!r}, which does not exist"
                raise MissingConfigError(msg)
            files.append((candidate, None))
            steps.append(Step(2, f"${self.config_var}", (candidate,)))
        else:
            steps.append(Step(2, f"${self.config_var}", note="not set"))

        # 3 - the project's own search roots.
        for root in self.roots(cwd):
            text = str(root)
            step, hits = self._scan(3, root, text if text.endswith("/") else f"{text}/", stems)
            steps.append(step)
            files.extend(hits)
            if hits and self.mode == "first":
                break

        # 4 - the developer's machine.
        if self.user_config:
            for pure in user_config_dirs(self.app, platform=platform, environ=env):
                directory = Path(str(pure))
                step, hits = self._scan(4, directory, str(directory), stems)
                steps.append(step)
                files.extend(hits)
        else:
            steps.append(Step(4, "user config dir", note="disabled"))

        # 5 - the repository's own default, handled by PyProjectSource.
        if self.table:
            steps.append(Step(5, f"pyproject.toml [{self.table}]", note="read separately"))
        else:
            steps.append(Step(5, "pyproject.toml", note="disabled"))

        if not files and self.on_missing == "error":
            msg = f"no configuration file found for {self.app!r}. Searched:\n" + "\n".join(
                f"  {s.label}" for s in steps
            )
            raise MissingConfigError(msg)
        return DiscoveryPlan(tuple(steps), tuple(files))

    def file_sources(self, plan: DiscoveryPlan) -> list[FileSource]:
        """Turn a plan into file sources, highest precedence first.

        Args:
            plan: A plan produced by :meth:`plan`.

        Returns:
            One source per discovered file.
        """
        return [FileSource(path, profile=profile) for path, profile in plan.files]


class PyProjectSource:
    """Reads a dotted table out of ``pyproject.toml``.

    The lowest file layer: a project's own committed defaults, which every other
    source is entitled to override.

    Args:
        path: The ``pyproject.toml`` to read.
        table: The dotted table name, such as ``"tool.myapp"``.
        name: The layer name.
    """

    def __init__(
        self, path: Path | str = "pyproject.toml", table: str = "", *, name: str = "pyproject"
    ) -> None:
        """Build a pyproject source."""
        self.path = Path(path)
        self.table = table
        self.name = name

    def load(self) -> Layer:
        """Read the table, if the file and the table both exist.

        Returns:
            The layer, with ``found=False`` when either is absent.

        Raises:
            FormatError: If ``pyproject.toml`` does not parse.
        """
        if not self.table or not self.path.is_file():
            return Layer(self.name, {}, found=False)
        text = self.path.read_text(encoding="utf-8-sig")
        try:
            data: object = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            msg = f"{self.path}: invalid TOML: {exc}"
            raise FormatError(msg) from exc
        for segment in self.table.split("."):
            if not isinstance(data, Mapping) or segment not in data:
                return Layer(self.name, {}, found=False)
            data = data[segment]
        if not isinstance(data, Mapping):
            msg = f"{self.path}: [{self.table}] must be a table"
            raise ConfigError(msg)
        origin = Origin("file", f"{self.path} [{self.table}]")
        entries: dict[KeyPath, Tracked] = {
            path: Tracked(value, origin) for path, value in flatten(data).items()
        }
        return Layer(self.name, entries, found=bool(entries))
