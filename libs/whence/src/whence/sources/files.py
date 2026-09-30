"""A single configuration file, parsed by whichever loader matches its suffix."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from ..errors import MissingConfigError
from ..formats import Loader, loader_for
from ..origin import Tracked
from ..tree import Layer

__all__ = ["FileSource"]


class FileSource:
    """Loads one configuration file.

    Args:
        path: The file to read.
        name: The layer name; defaults to the path itself, which is what makes
            ``explain`` readable when several files are layered.
        optional: When false, a missing file raises. An explicitly named file
            that does not exist is always a mistake; a discovered one is not.
        profile: The profile this file belongs to, recorded in every origin it
            produces.
        loaders: A suffix-to-loader table; defaults to the built-ins.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        name: str | None = None,
        optional: bool = True,
        profile: str | None = None,
        loaders: Mapping[str, Loader] | None = None,
    ) -> None:
        """Build a file source."""
        self.path = Path(path)
        self.name = name or str(path)
        self.optional = optional
        self.profile = profile
        self._loaders = loaders

    def load(self) -> Layer:
        """Read and parse the file.

        Returns:
            The layer, with ``found=False`` when an optional file is absent.

        Raises:
            MissingConfigError: If a required file does not exist.
            FormatError: If the file cannot be parsed.
        """
        if not self.path.is_file():
            if self.optional:
                return Layer(self.name, {}, found=False)
            msg = f"configuration file {self.path} does not exist"
            raise MissingConfigError(msg)
        text = self.path.read_text(encoding="utf-8-sig")
        parse = loader_for(self.path.suffix, self._loaders)
        entries = parse(text, str(self.path))
        if self.profile is not None:
            entries = {
                path: Tracked(value.value, replace(value.origin, profile=self.profile))
                for path, value in entries.items()
            }
        return Layer(self.name, entries, found=True)
