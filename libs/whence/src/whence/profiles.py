"""Active profiles, profile groups, and the one invariant that matters.

Profiles select *documents*. They never reorder sources.

That sentence is the whole design. Spring learned it the hard way: before 2.4 a
profile-specific document could itself activate profiles, so ordering was
emergent rather than lexical and the framework had to be redesigned around it.
Quarkus states the rule directly -- a profiled key cannot let a lower-ordinal
source outrank a higher one -- and it eliminates the entire "why did the dev
configuration win in production?" class of incident.

Profile *lists are ordered*, and later wins. Treating them as a set is the
mistake behind more than one production incident, so ``explain`` always prints
the resolved order.
"""

from collections.abc import Mapping, Sequence

from .errors import ConfigError

__all__ = ["MAX_GROUP_DEPTH", "active_profiles", "expand_groups"]

MAX_GROUP_DEPTH = 8
"""How far a group may expand into other groups before it is called a cycle."""


def expand_groups(
    profiles: Sequence[str],
    groups: Mapping[str, Sequence[str]] | None = None,
    _depth: int = 0,
) -> tuple[str, ...]:
    """Expand profile groups into the profiles they stand for.

    ``groups={"staging": ["cloud", "readonly-db"]}`` turns ``["staging"]`` into
    ``["cloud", "readonly-db", "staging"]``. Spring added groups in 2.4 to
    replace chained ``include`` directives, and they are considerably easier to
    read than the chain they replaced.

    Args:
        profiles: The requested profiles, in increasing precedence.
        groups: Group definitions.
        _depth: Recursion guard.

    Returns:
        The expanded profiles, deduplicated, in increasing precedence.

    Raises:
        ConfigError: If groups reference each other in a cycle.
    """
    if _depth > MAX_GROUP_DEPTH:
        msg = f"profile groups nest more than {MAX_GROUP_DEPTH} deep; check for a cycle"
        raise ConfigError(msg)
    table = {k.strip().lower(): v for k, v in (groups or {}).items()}
    out: list[str] = []
    for raw in profiles:
        name = raw.strip().lower()
        if not name:
            continue
        members = table.get(name)
        if members:
            out.extend(expand_groups(list(members), table, _depth + 1))
        out.append(name)
    # dict preserves first-insertion order, which is the dedup this needs.
    return tuple(dict.fromkeys(out))


def active_profiles(
    explicit: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    var: str = "",
    groups: Mapping[str, Sequence[str]] | None = None,
    default: Sequence[str] = (),
) -> tuple[str, ...]:
    """Decide which profiles are active.

    Args:
        explicit: Profiles passed in code; wins over the environment.
        environ: The environment to consult.
        var: The variable holding a comma-separated list.
        groups: Group definitions to expand.
        default: Profiles to use when nothing else says.

    Returns:
        Active profiles in increasing precedence, so the last one wins.
    """
    if explicit is not None:
        chosen: Sequence[str] = explicit
    elif var and environ and (raw := environ.get(var, "").strip()):
        chosen = raw.split(",")
    else:
        chosen = default
    return expand_groups(chosen, groups)
