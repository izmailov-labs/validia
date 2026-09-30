"""A key-per-file secrets directory: the Docker and Kubernetes mount shape.

Docker Swarm mounts secrets at ``/run/secrets/<name>`` on an in-memory
filesystem, and Kubernetes projects them the same way. Spring calls this
``configtree:``, .NET calls it ``AddKeyPerFile``; Python has had no standard
answer, which is why so much code reads these files by hand.

whence places this source *above* configuration files rather than below them.
pydantic-settings puts its secrets directory at the bottom of the stack, which
means a checked-in default silently wins over a mounted production secret. A
secret that an operator deliberately mounted is deployment truth.

The directory rarely exists outside a container and never on Windows, so a
missing directory is not an error -- it is a layer that reports ``found=False``.
"""

from pathlib import Path

from ..keys import KeyPath, canonical
from ..origin import Origin, Tracked
from ..tree import Layer

__all__ = ["SecretsDirSource"]


class SecretsDirSource:
    """Reads one file per key from a directory.

    A file named ``db.password`` or ``db__password`` becomes the key
    ``db.password``; both spellings work because Kubernetes secret keys cannot
    always contain dots.

    Args:
        path: The directory to read.
        name: The layer name.
    """

    def __init__(self, path: Path | str = "/run/secrets", *, name: str = "secrets-dir") -> None:
        """Build a secrets-directory source."""
        self.path = Path(path)
        self.name = name

    def load(self) -> Layer:
        """Read every regular file in the directory.

        Returns:
            The layer, with ``found=False`` when the directory is absent.
        """
        if not self.path.is_dir():
            return Layer(self.name, {}, found=False)
        entries: dict[KeyPath, Tracked] = {}
        for child in sorted(self.path.iterdir()):
            # Kubernetes projects secrets through a `..data` symlink and hides
            # the real files under `..2024_01_01_00_00_00.123456789/`.
            if child.name.startswith(".") or not child.is_file():
                continue
            text = child.read_text(encoding="utf-8")
            key = canonical(child.name.replace("__", "."))
            entries[key] = Tracked(text.rstrip("\r\n"), Origin(self.name, str(child)))
        return Layer(self.name, entries, found=bool(entries))
