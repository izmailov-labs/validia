"""validia — a universal, async-first evaluation framework for LLM systems.

Covers the full evaluation spectrum under one set of primitives: prompt
evaluation, tool selection, agent evaluation, and agent-type comparison.

The public API is re-exported from this module; everything not listed in
``__all__`` is internal and may change without a major version bump. It is the
same API whatever the front end: the ``validia`` command calls these functions,
and so can a REST service or a notebook -- see :mod:`validia.suites.api`.
"""

from importlib.metadata import PackageNotFoundError, version

from .prompts.template import PromptTemplate, TemplateError, default_template, load_template
from .rules import (
    CATEGORIES,
    Finding,
    Guidance,
    Part,
    Release,
    Rule,
    RuleCheck,
    RuleError,
    RulePack,
    core_categories,
    core_releases,
    core_rules,
    core_targets,
    core_versions,
    default_rules,
    lint_text,
    load_rules,
    verify_core,
    verify_project,
    verify_rules,
)
from .rules.local import (
    Written,
    disable_rule,
    extend_rule,
    fall_back,
    new_rule,
    replace_rule,
    rule_target,
)
from .runs.runner import (
    RunSummary,
    Trial,
    TrialResult,
    build_model,
    run_trial,
    summarize,
    trials,
    unsupported,
    wilson,
)
from .suites.api import (
    CLOSING,
    BuildError,
    CaseSpec,
    Option,
    Question,
    SpecError,
    SuiteSpec,
    add_case,
    check_reply,
    closing_options,
    create_suite,
    describe_suite,
    find_case,
    locate_suite,
    parse_reply,
    prompt_questions,
    render_prompt,
    review_answer,
    review_answers,
)
from .suites.expect import Reply, ToolCall, Verdict
from .suites.suite import Case, Grade, Suite, SuiteError, Tool, load_suite

__all__ = [
    "CATEGORIES",
    "CLOSING",
    "BuildError",
    "Case",
    "CaseSpec",
    "Finding",
    "Grade",
    "Guidance",
    "Option",
    "Part",
    "PromptTemplate",
    "Question",
    "Release",
    "Reply",
    "Rule",
    "RuleCheck",
    "RuleError",
    "RulePack",
    "RunSummary",
    "SpecError",
    "Suite",
    "SuiteError",
    "SuiteSpec",
    "TemplateError",
    "Tool",
    "ToolCall",
    "Trial",
    "TrialResult",
    "Verdict",
    "Written",
    "__version__",
    "add_case",
    "build_model",
    "check_reply",
    "closing_options",
    "core_categories",
    "core_releases",
    "core_rules",
    "core_targets",
    "core_versions",
    "create_suite",
    "default_rules",
    "default_template",
    "describe_suite",
    "disable_rule",
    "extend_rule",
    "fall_back",
    "find_case",
    "lint_text",
    "load_rules",
    "load_suite",
    "load_template",
    "locate_suite",
    "new_rule",
    "parse_reply",
    "prompt_questions",
    "render_prompt",
    "replace_rule",
    "review_answer",
    "review_answers",
    "rule_target",
    "run_trial",
    "summarize",
    "trials",
    "unsupported",
    "verify_core",
    "verify_project",
    "verify_rules",
    "wilson",
]

try:
    __version__ = version("validia")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0.dev0"
