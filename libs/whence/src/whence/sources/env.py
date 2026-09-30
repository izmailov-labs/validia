"""Environment variables, mapped bijectively and with ``_FILE`` indirection.

Two platform notes that decide the design.

Windows environment variables are case-insensitive, and Python's ``os.environ``
reflects that by upper-casing keys there. whence sidesteps the difference
entirely by only ever looking for upper-case names, so the same code and the
same tests behave identically on all three platforms.

``_FILE`` indirection is the Docker and Kubernetes convention: ``DB_PASSWORD_FILE``
points at ``/run/secrets/db_password`` and the *contents* become the value. It
exists because environment variables leak -- they are inherited by every child
process, appear in crash dumps, and ``kubectl describe pod`` prints them. Docker
chose not to set secrets as environment variables for exactly this reason.
"""

import os
from collections.abc import Collection, Mapping
from pathlib import Path

from ..errors import ConfigError
from ..keys import KeyPath, canonical, env_name, key_from_env, validate_prefix
from ..origin import Origin, Tracked
from ..tree import Layer

__all__ = ["FILE_SUFFIX", "EnvSource"]

FILE_SUFFIX = "_FILE"
"""Appended to a variable name to point at a file holding the value."""


class EnvSource:
    """Reads configuration from environment variables.

    Args:
        prefix: The literal prefix every variable carries. Applied to the
            environment only -- files are never prefixed, matching Spring's
            ``setEnvironmentPrefix``.
        environ: The environment to read; defaults to ``os.environ``. Passing an
            explicit mapping is what makes a test hermetic.
        name: The layer name.
        aliases: An explicit map from variable name to dotted key, for the cases
            convention does not cover (``DATABASE_URL`` -> ``db.url``). An
            explicit, greppable table beats implicit name mangling, which is the
            lesson from node-config's ``custom-environment-variables.json``.
        read_files: Whether to honour the ``_FILE`` convention.
        exclude: Variables that control *how* configuration is found rather than
            carrying any of it -- ``$MYAPP_CONFIG`` and ``$MYAPP_PROFILES``.
            Without this they would be read twice: once by discovery, and again
            as data under a key named ``config``, which a schema that forbids
            unknown keys then rejects.
    """

    def __init__(
        self,
        prefix: str = "",
        *,
        environ: Mapping[str, str] | None = None,
        name: str = "env",
        aliases: Mapping[str, str] | None = None,
        read_files: bool = True,
        exclude: Collection[str] = (),
    ) -> None:
        """Build an environment source."""
        self.name = name
        self.exclude = frozenset(exclude)
        self.prefix = validate_prefix(prefix)
        # `environ or os.environ` would quietly reinstate the real environment
        # for a test that deliberately passed an empty one.
        self._environ = os.environ if environ is None else environ
        self._aliases = dict(aliases or {})
        self._read_files = read_files

    def name_for(self, path: KeyPath) -> str:
        """Return the one variable name that maps to a key path.

        Args:
            path: The canonical key path.

        Returns:
            ``"MYAPP_DB__HOST"``.
        """
        return env_name(path, self.prefix)

    def load(self) -> Layer:
        """Read every matching variable, resolving ``_FILE`` indirection.

        Returns:
            The layer.

        Raises:
            ConfigError: If both ``VAR`` and ``VAR_FILE`` are set, or a
                ``_FILE`` target cannot be read.
        """
        entries: dict[KeyPath, Tracked] = {}
        for raw_name, raw_value in sorted(self._environ.items()):
            if raw_name in self.exclude:
                continue
            alias = self._aliases.get(raw_name)
            if alias is not None:
                entries[canonical(alias)] = Tracked(raw_value, Origin(self.name, raw_name))
                continue
            is_file = self._read_files and raw_name.endswith(FILE_SUFFIX)
            lookup = raw_name[: -len(FILE_SUFFIX)] if is_file else raw_name
            path = key_from_env(lookup, self.prefix)
            if path is None:
                continue
            if not is_file:
                entries[path] = Tracked(raw_value, Origin(self.name, raw_name))
                continue
            # Setting both is always a mistake, and silently preferring one is
            # how a rotated secret goes unnoticed. Docker's own helper errors.
            if lookup in self._environ:
                msg = f"both {lookup} and {raw_name} are set; use exactly one to supply this value"
                raise ConfigError(msg)
            entries[path] = Tracked(self._read(raw_value, raw_name), Origin(self.name, raw_name))
        return Layer(self.name, entries, found=bool(entries))

    def _read(self, target: str, var: str) -> str:
        """Read a ``_FILE`` target, stripping the trailing newline."""
        path = Path(target)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"{var} points at {target!r}, which could not be read: {exc.strerror}"
            raise ConfigError(msg) from exc
        # `$(< file)` strips trailing newlines, and every editor adds one.
        return text.rstrip("\r\n")
