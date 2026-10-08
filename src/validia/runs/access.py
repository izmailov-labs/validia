"""Model access: which provider serves a model, and where its API key comes from.

A model is named ``provider:model``, as franca names it -- ``anthropic:claude-sonnet-5-5``.
The provider may be left out for the vendors whose model names say who they are
(``claude-...``, ``gpt-...``); :data:`PREFIXES` is that list, written out rather than
guessed, so an unfamiliar name is an error that asks for the provider instead of a
wrong guess.

API keys never live in settings. franca reads them from the environment at call time,
in a fixed order; :func:`key_source` names the place that order will find one, so a
dry run can say "key from ANTHROPIC_API_KEY" -- or exactly what to set -- before
anything is spent. It reports the variable's name only, never its value.

The environment a key is looked up in is the process's over the project's ``.env``
(:class:`KeyEnvironment`): a key exported in the shell outranks one in the file, as
every ``.env`` convention has it, and a key in the file needs no export at all. The
runner hands franca this same environment, so what the dry run reports is what the
call will use.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from franca.core.ids import ANTHROPIC, DEEPSEEK, GOOGLE, OPENAI, XAI
from franca.core.keys import VENDOR_ENV
from franca.core.settings import ProviderSettings

# Exported by its module, not yet by whence's top level: it parses a .env file into
# variable names with their lines, which is exactly what key lookup needs.
from whence.sources.dotenv import parse_dotenv

__all__ = [
    "KNOWN_PROVIDERS",
    "PREFIXES",
    "AccessError",
    "KeyEnvironment",
    "ModelSpec",
    "key_source",
    "key_variables",
    "parse_model",
]

KNOWN_PROVIDERS: tuple[str, ...] = tuple(VENDOR_ENV)
"""The providers franca knows a conventional key variable for."""

PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        "claude-": ANTHROPIC,
        "gpt-": OPENAI,
        "gemini-": GOOGLE,
        "grok-": XAI,
        "deepseek-": DEEPSEEK,
    }
)
"""Model-name prefixes that identify their provider without a ``provider:`` part."""


class AccessError(ValueError):
    """A model cannot be reached as configured; the message says what to change."""


@dataclass(frozen=True)
class KeyEnvironment(Mapping[str, str]):
    """The variables API keys are looked up in: the process's, over ``.env`` files.

    A read-only mapping, so it goes wherever franca takes an environment. Values are
    held, never shown: :meth:`where` names the place a variable came from.

    Attributes:
        process: The process environment; it outranks every file.
        files: Variables read from ``.env`` files, with their origins.
    """

    process: Mapping[str, str]
    files: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def load(
        cls, process: Mapping[str, str], paths: Sequence[Path], base: Path
    ) -> "KeyEnvironment":
        """Read ``.env`` files under the process environment; the first file wins.

        Args:
            process: The process environment.
            paths: ``.env`` files, highest precedence first; missing ones are skipped.
            base: Origins are shown relative to this, when inside it.

        Returns:
            The environment.

        Raises:
            whence.FormatError: If a file has an unterminated quoted value.
        """
        files: dict[str, tuple[str, str]] = {}
        for path in paths:
            if not path.is_file():
                continue
            shown = path.relative_to(base) if path.is_relative_to(base) else path
            for name, tracked in parse_dotenv(
                path.read_text(encoding="utf-8-sig"), str(shown)
            ).items():
                files.setdefault(
                    name, (str(tracked.value), f"{tracked.origin.locator}:{tracked.origin.line}")
                )
        return cls(process, files)

    def __getitem__(self, name: str) -> str:
        """Return a variable's value: the process's, unless it is empty there.

        An exported but empty variable counts as unset, as it does for franca, so
        it never hides a real key in a file.
        """
        if self.process.get(name):
            return self.process[name]
        if name in self.files:
            return self.files[name][0]
        return self.process[name]

    def __iter__(self) -> Iterator[str]:
        """Iterate every variable name, once."""
        yield from self.process
        yield from (name for name in self.files if name not in self.process)

    def __len__(self) -> int:
        """Count every variable name, once."""
        return len(self.process) + sum(1 for name in self.files if name not in self.process)

    def where(self, name: str) -> str:
        """Name the place a variable comes from, for a message; never its value.

        Args:
            name: The variable.

        Returns:
            ``the environment``, or a file and line such as ``.env:2``.
        """
        if self.process.get(name):
            return "the environment"
        return self.files[name][1] if name in self.files else "nowhere"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model, and the provider that serves it.

    Attributes:
        provider: The provider's slug, as in ``anthropic``.
        name: The model's name at that provider.
    """

    provider: str
    name: str

    def __str__(self) -> str:
        """Render as ``provider:model``, the form franca takes."""
        return f"{self.provider}:{self.name}"


def parse_model(spec: str) -> ModelSpec:
    """Split a model setting into provider and model.

    Args:
        spec: ``provider:model``, or a model name :data:`PREFIXES` recognises.

    Returns:
        The parsed spec.

    Raises:
        AccessError: If either half is empty, or the provider cannot be told.
    """
    provider, sep, name = spec.partition(":")
    if sep:
        if not provider or not name:
            msg = f"model {spec!r} needs both halves, as in anthropic:claude-sonnet-5-5"
            raise AccessError(msg)
        return ModelSpec(provider, name)
    for prefix, known in PREFIXES.items():
        if spec.startswith(prefix):
            return ModelSpec(known, spec)
    msg = f"cannot tell which provider serves {spec!r}; write it as provider:model, as in openai:{spec}"
    raise AccessError(msg)


def key_variables(provider: str, providers: Mapping[str, ProviderSettings]) -> list[str]:
    """List the environment variables franca reads a provider's key from, in order.

    Args:
        provider: The provider's slug.
        providers: The ``[providers]`` settings.

    Returns:
        The variable named by ``api_key_env``, if any, then
        ``FRANCA_<PROVIDER>_API_KEY``, then the vendor's own, if it has one.
    """
    names: list[str] = []
    configured = providers.get(provider)
    if configured is not None and configured.api_key_env:
        names.append(configured.api_key_env)
    names.append(f"FRANCA_{provider.upper()}_API_KEY")
    vendor = VENDOR_ENV.get(provider)  # type: ignore[call-overload]  # a slug is a Provider
    if vendor:
        names.append(vendor)
    return names


def key_source(
    provider: str, providers: Mapping[str, ProviderSettings], environ: Mapping[str, str]
) -> str | None:
    """Name where the provider's key will come from, in franca's own lookup order.

    Args:
        provider: The provider's slug.
        providers: The ``[providers]`` settings.
        environ: The environment franca will read.

    Returns:
        ``providers.<name>.api_key`` when one is set in settings, else the first
        variable from :func:`key_variables` that holds a value, else ``None``.
    """
    configured = providers.get(provider)
    if configured is not None and configured.api_key is not None:
        return f"providers.{provider}.api_key"
    return next((name for name in key_variables(provider, providers) if environ.get(name)), None)
