"""Sources: the things a configuration layer can come from.

A source is asked once, synchronously, for everything it has. Configuration is
read before the application runs, so there is no event loop for a source to
yield to and nothing to be gained by pretending otherwise: a Vault or Secrets
Manager source blocks the startup it is already part of, which is what starting
up means. The blocking SDK every cloud-secret backend already ships is
therefore the right shape, not a workaround.

A source that finds nothing is not an error and not silence: it returns a layer
with ``found=False`` so that ``explain`` can still show it was consulted.
"""

from typing import Protocol, runtime_checkable

from ..tree import Layer
from .argv import ArgvSource
from .dotenv import DotEnvSource, parse_dotenv
from .env import EnvSource
from .files import FileSource
from .mapping import MappingSource
from .secrets import SecretsDirSource

__all__ = [
    "ArgvSource",
    "DotEnvSource",
    "EnvSource",
    "FileSource",
    "MappingSource",
    "SecretsDirSource",
    "Source",
    "parse_dotenv",
]


@runtime_checkable
class Source(Protocol):
    """Something that can contribute one layer of configuration."""

    name: str

    def load(self) -> Layer:
        """Read everything this source has to offer.

        Returns:
            One layer, possibly empty, named after this source.
        """
        ...
