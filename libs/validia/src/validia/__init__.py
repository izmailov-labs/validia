"""validia — a universal, async-first evaluation framework for LLM systems.

Covers the full evaluation spectrum under one set of primitives: prompt
evaluation, tool selection, agent evaluation, and agent-type comparison.

The public API is re-exported from this module; everything not listed in
``__all__`` is internal and may change without a major version bump.
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("validia")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0.dev0"
