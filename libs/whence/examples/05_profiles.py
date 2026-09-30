"""5 -- Profiles: the same application, deployed twice.

A profile overlays one file on another: `demo.prod.toml` carries only what
differs from `demo.toml`. Profiles never reorder the precedence stack -- an
overlay outranks its base file and nothing else, so the environment still wins
over both.

Run it::

    uv run python 05_profiles.py
    DEMO_PROFILES=prod python 05_profiles.py    # the same switch, from outside

This example loads the same files twice in one process to put the two results
side by side; a real application loads once and lets `$DEMO_PROFILES` decide.
"""

from collections.abc import Sequence
from pathlib import Path

from whence import Config, Discovery, expand_groups

HERE = Path(__file__).parent
GROUPS = {"cloud": ("prod", "metrics")}


def load(profiles: Sequence[str] | None) -> Config:
    """Load the example configuration under a set of profiles.

    Args:
        profiles: Active profiles, or None to read `$DEMO_PROFILES`.

    Returns:
        The resolved configuration.
    """
    return Config.load(
        discovery=Discovery("demo", path=(Path("config"),), dotenv=(), secrets_dir=None),
        cwd=HERE,
        profiles=profiles,
        groups=GROUPS,
        environ={} if profiles is not None else None,
        argv=(),
    )


def main() -> int:
    """Load with and without the prod overlay, and compare.

    Returns:
        A process exit code.
    """
    base = load([])
    prod = load(["prod"])

    print("the same keys, two deployments")
    print(f"  {'key':<14} {'(no profile)':<18} prod")
    for key in ("db.host", "db.pool_size", "db.port"):
        print(f"  {key:<14} {base.get(key)!s:<18} {prod.get(key)}")
    print("\n  db.port is in neither overlay, so it comes from the base file in both.")

    print("\nwhere the prod values came from")
    print(prod.explain("db.host"))

    print("\nthe origin records the profile, not just the file")
    origin = prod.origin("db.host")
    print(f"  file    {origin.locator if origin else None}")
    print(f"  profile {origin.profile if origin else None}")

    print("\ngroups -- one name standing for several profiles")
    print(f"  groups={GROUPS}")
    print(f"  ['cloud'] expands to {list(expand_groups(['cloud'], GROUPS))}")
    print("  (`metrics` has no overlay file here, so it simply finds nothing)")

    print("\nfrom outside the process")
    live = load(None)
    print(f"  $DEMO_PROFILES -> {list(live.profiles) or '(unset)'}")
    print("  try: DEMO_PROFILES=prod python 05_profiles.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
