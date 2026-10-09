"""Running rules over text, and proving each rule and example against its cases."""

from collections import Counter

from .model import Finding, RuleCheck, RulePack, Scope, check_for


def lint_text(
    text: str,
    pack: RulePack,
    *,
    model: str | None = None,
    scope: Scope = "prompt",
) -> list[Finding]:
    """Run every rule for a kind of text over it.

    Args:
        text: The prompt, or one tool's description.
        pack: The rules.
        model: The model name severities are read for; the pack's own when omitted.
        scope: What kind of text it is.

    Returns:
        Every finding, ordered by position.
    """
    name = pack.model if model is None else model
    found = [
        finding for rule in pack.rules if scope in rule.scope for finding in rule.scan(text, name)
    ]
    return sorted(found, key=lambda finding: (finding.line, finding.column, finding.rule))


def verify_rules(pack: RulePack) -> list[RuleCheck]:
    """Prove every rule against its own cases, then every example.

    An example is linted with its own category's rules, as its own model sees them, so
    a layer's examples prove the severities that layer sets. A ``model`` rule's cases,
    and the examples of a category of them, wait for a model: they say what the text
    means, and a pattern is not held to that.

    Args:
        pack: The rules.

    Returns:
        One check per rule, then one per example.
    """
    checks: list[RuleCheck] = []
    for rule in pack.rules:
        if rule.check == "model":
            checks.append(RuleCheck(rule.id, (), 0, rule.category, waiting=True))
            continue
        failures = [f"should fire on: {case!r}" for case in rule.fires if not rule.scan(case)]
        failures += [f"should stay quiet on: {case!r}" for case in rule.quiet if rule.scan(case)]
        cases = len(rule.fires) + len(rule.quiet)
        checks.append(RuleCheck(rule.id, tuple(failures), cases, rule.category))
    for example in pack.examples:
        if check_for(example.category) == "model":
            checks.append(RuleCheck(example.name, (), 0, example.category, waiting=True))
            continue
        scoped = pack.only([example.category]) if example.category else pack
        found = lint_text(example.text, scoped, model=example.model, scope=example.scope)
        fired = Counter(finding.rule for finding in found)
        failures = [f"{rule} should fire" for rule in sorted(example.fires - set(fired))]
        failures += [f"{rule} fired but should not" for rule in sorted(set(fired) - example.fires)]
        failures += [
            f"{rule} should fire {count} times, fired {fired[rule]}"
            for rule, count in sorted(example.counts.items())
            if fired[rule] != count
        ]
        for rule_id, severity in sorted(example.severities.items()):
            seen = sorted({finding.severity for finding in found if finding.rule == rule_id})
            if rule_id in fired and seen != [severity]:
                failures.append(f"{rule_id} should be {severity}, was {', '.join(seen)}")
        checks.append(RuleCheck(example.name, tuple(failures), 1, example.category))
    return checks
