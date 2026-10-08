"""Settings for the ``validia`` command, resolved through whence.

A value can come from a command-line flag, ``--set key=value``, a ``VALIDIA_*``
environment variable, ``.env``, a ``validia.toml`` settings file, ``[tool.validia]``
in ``pyproject.toml``, or the defaults declared here, in that order of precedence.
whence records which one won, which is what lets ``validia config <key>`` answer
"why is this value what it is?" with a file and a line rather than a guess.

A suite can carry settings of its own: a ``validia.toml`` in the suite's folder
outranks the project's, so one suite can run another model, more reps, or a
different provider without touching the rest (:func:`for_suite`).

Model access -- ``[providers.<name>]`` with ``api_key_env``, ``base_url`` and
``timeout_s`` -- is franca's own schema, bound onto franca's own models so a typo
such as ``timeout = 30`` fails with its file and line. API keys stay out of settings:
``api_key_env`` names the variable that holds one (see :mod:`validia.runs.access`).
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from franca.core.settings import ProviderSettings
from franca.core.settings import Settings as FrancaSettings
from whence import BindError, Config, Discovery, Problem, bind, render_problems, settings

from validia.rules import is_pin

__all__ = [
    "DISCOVERY",
    "KEYS",
    "SETTINGS_FILE",
    "LintSettings",
    "RuleVersions",
    "RunSettings",
    "Settings",
    "defaults",
    "for_suite",
    "load",
]

DISCOVERY = Discovery(app="validia")
"""Where settings are searched for: ``validia.toml`` in the working directory,
``$VALIDIA_CONFIG``, the per-user config directory, and ``[tool.validia]``."""

SETTINGS_FILE = "validia.toml"
"""The file name discovery looks for, and the one ``validia init`` writes."""


@dataclass(frozen=True, slots=True)
class RunSettings:
    """How ``validia run`` executes a suite.

    Attributes:
        reps: Repetitions of every case. More than one is what makes a pass
            rate an estimate with an interval rather than a single draw.
        concurrency: Cases in flight at once.
        retries: Extra attempts for a call that failed in a way worth retrying: a rate
            limit, an overloaded server, a dropped connection.
        output: Directory run results are written under.
        fail_under: The pass rate, in percent, below which ``validia run`` exits 1;
            unset, only errors fail a run.
    """

    reps: int = 1
    concurrency: int = 4
    retries: int = 2
    output: Path = Path(".validia/runs")
    fail_under: float | None = None


@dataclass(frozen=True, slots=True)
class RuleVersions:
    """Which version of each core rule category ``lint`` uses.

    Each is ``latest``, to move forward with every release, or a version to stay on:
    ``"1"`` takes the newest 1.x.y, ``"1.2"`` the newest 1.2.y, ``"1.2.3"`` exactly
    that. A pin covers every layer of its category -- the default, the vendor's and
    the model's -- so one category can stay behind while the rest move on.

    Attributes:
        wording: How each instruction is phrased.
        context: What the window holds and how it is laid out.
        reasoning: Scripts, coaching and thinking in prose.
        output: The output contract.
        tools: Tool definitions.
        security: Secrets, untrusted input, leaks.
        maintenance: Grader talk and fossils from older models.
    """

    wording: str = "latest"
    context: str = "latest"
    reasoning: str = "latest"
    output: str = "latest"
    tools: str = "latest"
    security: str = "latest"
    maintenance: str = "latest"

    def pinned(self) -> dict[str, str]:
        """Map each category to its pin.

        Returns:
            Category names to ``latest`` or a version.
        """
        return {category: str(value) for category, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class LintSettings:
    """How ``validia lint`` decides a prompt fails.

    Attributes:
        fail_on: The lowest finding severity that fails the command.
        rules: The version of each core rule category.
    """

    fail_on: Literal["error", "warning"] = "error"
    rules: RuleVersions = field(default_factory=RuleVersions)


@settings()
@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the ``validia`` command can be configured with.

    Attributes:
        model: The model under evaluation, as ``provider:model`` or a model name
            whose provider is plain from it (see :mod:`validia.runs.access`).
        run: Settings for ``validia run``.
        lint: Settings for ``validia lint``.
        providers: How to reach each provider, as franca's ``ProviderSettings``.
    """

    model: str | None = None
    run: RunSettings = field(default_factory=RunSettings)
    lint: LintSettings = field(default_factory=LintSettings)
    providers: dict[str, ProviderSettings] = field(default_factory=dict)


PROVIDERS = "providers"
"""The table franca's provider settings live under, bound by franca's own models."""


def _keys(tree: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    """Yield the dotted key of every leaf in a nested mapping, in order."""
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from _keys(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}"


KEYS = tuple(_keys(asdict(Settings())))
"""Every setting's dotted key, in declaration order, including unset ones."""


def _plain(value: Any) -> Any:
    """Reduce a defaults tree to what a settings file could have said."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, Path):
        return value.as_posix()
    return value


def defaults() -> dict[str, Any]:
    """Return the schema's defaults as a nested mapping.

    Loading them as a source, rather than leaving them on the dataclass, puts
    them in the namespace with a ``<defaults>`` origin, so ``validia config``
    lists every setting and not only the ones somebody wrote down.

    Returns:
        The defaults, with unset values left out.
    """
    plain: dict[str, Any] = _plain(asdict(Settings()))
    plain.pop(PROVIDERS)
    return plain


def for_suite(discovery: Discovery, folder: Path, cwd: Path) -> Discovery:
    """Search a suite's folder for settings first, so its ``validia.toml`` wins.

    Explicit choices still outrank it: flags, ``--set``, the environment, ``--config``
    and ``$VALIDIA_CONFIG`` all sit above discovered files. Only the project's own
    ``validia.toml`` and everything below it give way.

    Args:
        discovery: The project's discovery settings.
        folder: The suite's folder.
        cwd: The working directory relative roots resolve against.

    Returns:
        Discovery with the suite's folder as the highest-precedence root.
    """
    roots = discovery.roots(cwd)
    if any(root.resolve() == folder.resolve() for root in roots):
        return discovery
    return discovery.with_(path=(folder, *discovery.path))


def _check(config: Config, bound: Settings) -> None:
    """Reject values the types allow but the commands cannot use.

    Raises:
        BindError: If any value is out of range, listing every one at once.
    """
    problems = [
        Problem(key, value, config.origin(key), "must be at least 1")
        for key, value in (("run.reps", bound.run.reps), ("run.concurrency", bound.run.concurrency))
        if value < 1
    ]
    if bound.run.retries < 0:
        problems.append(
            Problem(
                "run.retries", bound.run.retries, config.origin("run.retries"), "must be 0 or more"
            )
        )
    under = bound.run.fail_under
    if under is not None and not 0 <= under <= 100:
        problems.append(
            Problem(
                "run.fail_under",
                under,
                config.origin("run.fail_under"),
                "must be a percentage, 0 to 100",
            )
        )
    for category, value in asdict(bound.lint.rules).items():
        if not is_pin(str(value)):
            key = f"lint.rules.{category}"
            reason = 'expected "latest" or a version, as in "1", "1.2" or "1.2.3"'
            problems.append(Problem(key, value, config.origin(key), reason))
    if problems:
        raise BindError(render_problems(problems))


def load(
    *,
    discovery: Discovery = DISCOVERY,
    profiles: Sequence[str] | None = None,
    argv: Sequence[str] = (),
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[Config, Settings]:
    """Resolve settings from every source and bind them onto :class:`Settings`.

    Args:
        discovery: Where settings files are searched for.
        profiles: Active profiles; falls back to ``$VALIDIA_PROFILES``.
        argv: Command-line arguments to scan for ``--set key=value``. Empty by
            default, so a library call never reads ``sys.argv`` behind your back.
        overrides: Values that outrank every source: the command's own flags.
        environ: The environment to read; defaults to ``os.environ``.
        cwd: The directory discovery starts from.

    Returns:
        The resolved configuration, which knows where every value came from,
        and the typed settings bound from it.

    Raises:
        whence.WhenceError: If a source fails to load, a key is unknown, or a
            value has the wrong type or is out of range.
    """
    config = Config.load(
        discovery=discovery,
        profiles=profiles,
        argv=argv,
        overrides=overrides,
        defaults=defaults(),
        environ=environ,
        cwd=cwd,
    )
    own = {path: value for path, value in config.values.items() if path[:1] != (PROVIDERS,)}
    access = {path: value for path, value in config.values.items() if path[:1] == (PROVIDERS,)}
    bound: Settings = bind(Settings, own)
    # franca's models forbid unknown fields themselves, so strict=False loses
    # nothing: `[providers.x] timeout = 30` still fails, with its origin.
    franca: FrancaSettings = bind(FrancaSettings, access, strict=False)
    bound = replace(bound, providers={str(name): value for name, value in franca.providers.items()})
    _check(config, bound)
    return config, bound
