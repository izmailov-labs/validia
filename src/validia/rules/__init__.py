"""Prompt rules: regular expressions over prompt text, each proving itself on its own cases.

A rule is data: a pattern, how severe a hit is, which part of a prompt it reads, and
what to do about it. Every rule has its own test cases -- text it must fire on and text
it must stay quiet on -- and a rule without both is refused when it loads.
:func:`verify_rules` runs them, with whole-prompt examples, so a pattern that has
drifted is caught before it runs on a real prompt.

**A folder per rule.** The core rules live one folder per rule, inside its category
(:data:`CATEGORIES`), and inside that, one folder per target::

    <category>/<rule>/default/1.0.0.toml               the rule, for every model
    <category>/<rule>/default/1.0.0.cases.toml         its cases
    <category>/<rule>/<provider>/default/1.0.0.toml    every model of one vendor
    <category>/<rule>/<provider>/<model>/1.0.0.toml    one model
    <category>/examples/<target>/1.0.0.toml            whole prompts, as that target sees them
    <category>/guidance/<target>/1.0.0.toml            how to write for that target

For a model, each rule is read along its chain -- default, the vendor's, the model's --
and each file is laid over the one before: it can extend the rule (a severity, a fix,
more cases), replace it, or turn it off. A rule folder with no default adds a rule only
for the models that have one.

**Releases.** A category is released as a whole, in semantic versions, and every file is
named for the release it changed in: a rule untouched since 1.0.0 keeps only
``1.0.0.toml``. The numbers make a promise about a lint gate: a patch only removes false
positives, a minor release adds only info findings or cases, and a major release can
add a warning or an error. A pin -- ``"1"``, ``"1.2"``, ``"1.2.3"`` or ``"latest"`` --
names a category release, and every file is read as it was at that release; a rule
added later does not exist yet. Released files never change.

**Projects.** A project's ``rules/`` folder has the same layout --
``rules/<category>/<rule>/default/1.0.0.toml``, its cases beside it, and vendor and model
folders -- and is read by the same code, after the core: each rule's chain runs through
the core's files, then the project's. It can define, extend, replace or turn off any
rule, for every model or for one, plus ``from = "1"``: one rule, along its whole core
chain, as it was at an older release. A rule the core does not have is a new rule, and a
category it does not have is a new category. The project reads its newest file in each
folder, and its files need no ``released`` or ``changes``.
"""

from .catalog import (
    core_categories,
    core_releases,
    core_rules,
    core_targets,
    core_versions,
    is_pin,
)
from .layering import (
    default_rules,
    load_rules,
    verify_core,
    verify_project,
)
from .matching import (
    lint_text,
    verify_rules,
)
from .model import (
    CATEGORIES,
    CHECKS,
    PROJECT_RULES,
    SEVERITIES,
    Check,
    Example,
    Finding,
    Guidance,
    Part,
    Release,
    Rule,
    RuleCheck,
    RuleError,
    RulePack,
    Scope,
    Severity,
    Target,
    check_for,
)

__all__ = [
    "CATEGORIES",
    "CHECKS",
    "PROJECT_RULES",
    "SEVERITIES",
    "Check",
    "Example",
    "Finding",
    "Guidance",
    "Part",
    "Release",
    "Rule",
    "RuleCheck",
    "RuleError",
    "RulePack",
    "Scope",
    "Severity",
    "Target",
    "check_for",
    "core_categories",
    "core_releases",
    "core_rules",
    "core_targets",
    "core_versions",
    "default_rules",
    "is_pin",
    "lint_text",
    "load_rules",
    "verify_core",
    "verify_project",
    "verify_rules",
]
