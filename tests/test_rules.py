"""The rule engine: rule shapes, a folder per rule, chains, releases, pins and projects."""

import hashlib
import re
from importlib import resources
from pathlib import Path

import pytest

from validia.rules import (
    CATEGORIES,
    Rule,
    RuleError,
    RulePack,
    core_categories,
    core_releases,
    core_rules,
    core_targets,
    core_versions,
    default_rules,
    is_pin,
    lint_text,
    load_rules,
    verify_core,
    verify_project,
    verify_rules,
)

MANIFEST = Path(__file__).parent / "data" / "released-rules.sha256"


def released_files() -> dict[str, str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    pairs = (line.split("  ", 1) for line in lines if line and not line.startswith("#"))
    return {path: digest for digest, path in pairs}


def test_released_core_files_are_frozen() -> None:
    """A released file never changes: a change is a new file, for the next release."""
    root = Path(str(resources.files("validia").joinpath("rules", "core")))
    shipped = {path.relative_to(root).as_posix(): path for path in root.rglob("*.toml")}
    frozen = released_files()
    assert set(shipped) == set(frozen), "a rule file was added or removed without its line"
    for name, digest in frozen.items():
        assert hashlib.sha256(shipped[name].read_bytes()).hexdigest() == digest, (
            f"{name} was edited"
        )


def test_every_core_target_proves_itself_at_every_release() -> None:
    for category in CATEGORIES:
        for release in core_releases(category):
            proved = verify_core(pins={category: release.version}, categories=[category])
            assert [target for _, target, _, _ in proved] == list(core_targets(category))
            for _, target, pack, checks in proved:
                failing = [check for check in checks if not check.passed]
                assert failing == [], f"{category}/{target} at {release.version}"
                assert all(rule.fires and rule.quiet for rule in pack.rules)


def test_core_files_cite_only_what_users_can_read() -> None:
    """The rule files ship to users: no pointer into validia's own working notes."""
    root = Path(str(resources.files("validia").joinpath("rules", "core")))
    for path in root.rglob("*.toml"):
        text = path.read_text(encoding="utf-8")
        assert "prompt-tables" not in text, path
        assert re.search(r"\bG[1-7]\b", text) is None, path


def test_the_core_is_a_folder_per_rule() -> None:
    assert core_categories() == CATEGORIES
    assert core_rules("reasoning") == (
        "numbered-script",
        "plan-first",
        "self-verify",
        "show-reasoning",
        "strategy-coaching",
        "think-in-prose",
    )
    assert core_targets("reasoning") == (
        "default",
        "anthropic/default",
        "anthropic/claude-opus-5",
    )
    assert core_versions("reasoning", "show-reasoning", "anthropic/default") == ("1.0.0",)
    assert core_versions("reasoning", "show-reasoning", "openai/default") == ()
    assert core_rules("nope") == ()
    (release,) = core_releases("reasoning")
    assert (release.version, release.released) == ("1.0.0", "2026-10-07")
    changes = dict(release.changes)
    assert changes["First release."][0] == "numbered-script default"
    assert any(where == ("show-reasoning anthropic/default",) for where in changes.values())


def test_a_model_reads_its_vendor_and_its_own_files() -> None:
    plain = default_rules()
    sonnet = default_rules(("anthropic", "claude-sonnet-5-5"))
    haiku = default_rules(("anthropic", "claude-haiku-4-5"))
    elicit = "Show your reasoning before the answer."

    def severities(pack: RulePack) -> list[str]:
        return [f.severity for f in lint_text(elicit, pack) if f.rule == "reasoning/show-reasoning"]

    assert (severities(plain), severities(sonnet), severities(haiku)) == (
        ["warn"],
        ["error"],
        ["warn"],
    )
    assert sonnet.version == "1.0.0 for anthropic:claude-sonnet-5-5"
    rule = next(r for r in sonnet.rules if r.id == "reasoning/show-reasoning")
    assert rule.origin == ("default 1.0.0", "anthropic/default 1.0.0 extend")
    assert ("context", "anthropic/claude-sonnet-5-5") in {
        (note.category, note.target) for note in sonnet.guidance
    }
    assert all(part.target == "default" for part in plain.parts)
    assert plain.guidance == ()
    assert all(note.summary and note.instructions and note.sources for note in sonnet.guidance)


# ------------------------------------------------------------- rule shapes


def rule(**fields: object) -> Rule:
    base: dict[str, object] = {"id": "X.test", "pattern": "x", "fix": "fix it"}
    return Rule(**{**base, **fields})  # type: ignore[arg-type]


def test_a_plain_rule_fires_per_hit_with_position() -> None:
    found = rule(pattern=r"\bmust\b").scan("You must.\nThey must not.")
    assert [(f.line, f.column, f.excerpt) for f in found] == [(1, 5, "must"), (2, 6, "must")]
    assert found[0].fix == "fix it"


def test_case_sensitivity() -> None:
    assert not rule(pattern="MUST", case_sensitive=True).scan("you must")
    assert rule(pattern="MUST").scan("you must")


def test_unless_looks_at_the_sentence_and_the_next() -> None:
    bare = rule(pattern=r"\bnever\b", unless=r"\bbecause\b")
    assert bare.scan("Never guess.")
    assert not bare.scan("Never guess, because it costs.")
    assert not bare.scan("Never guess. It costs, because customers act on it.")
    assert bare.scan("Never guess. Be brief. It costs, because customers act on it.")


def test_a_run_counts_consecutive_sentences_or_lines() -> None:
    cluster = rule(pattern=r"^\s*never\b", run=3)
    found = cluster.scan("Never a. Never b. Never c. Fine. Never d.")
    assert [(f.column, f.excerpt) for f in found] == [(1, "3 in a row: Never a.")]
    assert not cluster.scan("Never a. Fine. Never b. Never c.")
    wall = rule(pattern=r"^\s*-\s", run=2, unit="line")
    assert len(wall.scan("- a\n- b\n\n- c\n- d")) == 2  # a blank line ends a list
    assert not rule(pattern=r"^\s*-\s", run=3, unit="line").scan("- a\n- b\n\n- c")
    assert not wall.scan("- a. - b")


def test_at_least_fires_once_on_enough_matches() -> None:
    many = rule(pattern=r"^if the user\b", at_least=2)
    found = many.scan("If the user a\nIf the user b\nIf the user c")
    assert [f.excerpt for f in found] == ["3 times: If the user"]
    assert not many.scan("If the user a")


def test_missing_fires_when_the_pattern_is_absent() -> None:
    whennot = rule(pattern="when not to", missing=True)
    assert [(f.line, f.excerpt) for f in whennot.scan("Look up an order.")] == [(1, "")]
    assert not whennot.scan("Look up an order. When not to: refunds.")


def test_severity_depends_on_the_model() -> None:
    caps = rule(severity="warn", models=("claude-opus-5*",), otherwise="info")
    assert caps.severity_for("claude-opus-5-5") == "warn"
    assert caps.severity_for("claude-opus-5") == "warn"
    assert caps.severity_for("claude-haiku-4-5") == "info"
    assert caps.severity_for(None) == "info"
    assert rule(severity="error").severity_for(None) == "error"


def test_excerpts_are_clipped_and_start_at_the_words() -> None:
    long = rule(pattern="x+").scan("x" * 80)[0]
    assert long.excerpt.endswith("...")
    assert len(long.excerpt) == 60
    boundary = rule(pattern=r"(^|[.]\s+)do not").scan("Fine. Do not.")[0]
    assert (boundary.column, boundary.excerpt) == (7, "Do not")
    assert rule(pattern="!!").scan("Hi!!")[0].excerpt == "!!"


def test_lint_reads_only_its_scope_and_sorts_by_position() -> None:
    pack = default_rules()
    findings = lint_text("You are a helpful assistant.\nTry to help.", pack)
    assert [(f.rule, f.line) for f in findings] == [
        ("context/identity-stub", 1),
        ("wording/hedged-requirement", 2),
    ]
    tools = lint_text("You MUST call this.", pack, scope="tool_description")
    assert {f.rule for f in tools} == {"tools/missing-when-not", "tools/capitals-in-tool"}


def test_a_pack_narrows_to_categories() -> None:
    pack = default_rules(("anthropic", "claude-opus-5-5"))
    wording = pack.only(["wording"])
    assert wording.categories == ("wording",)
    assert {rule.category for rule in wording.rules} == {"wording"}
    assert {part.category for part in wording.parts} == {"wording"}
    assert {note.category for note in wording.guidance} == {"wording"}
    assert all(example.category == "wording" for example in wording.examples)
    assert wording.target == pack.target
    assert default_rules(categories=["tools", "security"]).categories == ("tools", "security")
    with pytest.raises(RuleError, match="no category 'tone'; there are wording, context"):
        default_rules(categories=["tone"])


# ------------------------------------------------------- a core of our own

DAYS = {"1.0.0": "2026-10-07", "1.1.0": "2026-11-01", "2.0.0": "2026-12-01"}


def head(version: str, change: str = "") -> str:
    return f'released = "{DAYS[version]}"\nchanges = ["{change or "Changed in " + version}."]\n\n'


def definition(title: str, pattern: str, severity: str, fix: str = "Fix it.") -> str:
    return f'title = "{title}"\npattern = \'{pattern}\'\nseverity = "{severity}"\nfix = "{fix}"\n'


def cases(fires: list[str], quiet: list[str] | None = None) -> str:
    out = f"fires = {fires!r}\n".replace("'", '"')
    return out + (f"quiet = {quiet!r}\n".replace("'", '"') if quiet is not None else "")


CORE = {
    # wording/bare: 1.0.0 catches "never"; 2.0.0 catches "always" too, and keeps its cases.
    "wording/bare/default/1.0.0.toml": head("1.0.0")
    + definition("A bare never", r"\bnever\b", "warn", "Give the reason."),
    "wording/bare/default/1.0.0.cases.toml": cases(["Never guess."], ["Ask when unsure."]),
    "wording/bare/default/2.0.0.toml": head("2.0.0")
    + definition("A bare rule", r"\b(never|always)\b", "warn", "Give the reason."),
    "wording/bare/default/2.0.0.cases.toml": cases(
        ["Never guess.", "Always cite."], ["Ask when unsure."]
    ),
    "wording/bare/acme/acme-big-2/1.0.0.toml": head("1.0.0")
    + 'extend = true\nfix = "Give the reason; Big 2 generalises from it."\n',
    "wording/bare/acme/acme-big-2/1.0.0.cases.toml": cases(["Never round."]),
    # wording/caps: info for everyone, a warning on the big acme models.
    "wording/caps/default/1.0.0.toml": head("1.0.0")
    + definition("Capitals", r"\bIMPORTANT\b", "info")
    + "case_sensitive = true\n",
    "wording/caps/default/1.0.0.cases.toml": cases(["IMPORTANT: be brief."], ["Be brief."]),
    "wording/caps/acme/default/1.0.0.toml": head("1.0.0")
    + 'extend = true\nseverity = "warn"\nmodels = ["acme-big*"]\n',
    # wording/hedge: added at 1.1.0, removed at 2.0.0.
    "wording/hedge/default/1.1.0.toml": head("1.1.0")
    + definition("A hedge", "try to", "info", "Make it firm."),
    "wording/hedge/default/1.1.0.cases.toml": cases(["Try to help."], ["Help."]),
    "wording/hedge/default/2.0.0.toml": head("2.0.0") + "enabled = false\n",
    # wording/acme-jargon: a rule only acme models have.
    "wording/acme-jargon/acme/default/1.0.0.toml": head("1.0.0")
    + definition("Acme jargon", "synergy", "warn"),
    "wording/acme-jargon/acme/default/1.0.0.cases.toml": cases(
        ["Find synergy."], ["Find savings."]
    ),
    "wording/examples/default/1.0.0.toml": head("1.0.0")
    + '[[examples]]\nname = "loud"\ntext = "IMPORTANT: never guess."\n'
    + 'fires = { "bare" = "warn", "caps" = "info" }\n',
    "wording/examples/acme/default/1.0.0.toml": head("1.0.0")
    + '[[examples]]\nname = "loud on big"\nmodel = "acme-big-1"\ntext = "IMPORTANT: brief."\n'
    + 'fires = { "caps" = "warn" }\n\n'
    + '[[examples]]\nname = "quiet on small"\nmodel = "acme-small"\ntext = "IMPORTANT: brief."\n'
    + 'fires = { "caps" = "info" }\n',
    "wording/examples/acme/acme-big-2/1.0.0.toml": head("1.0.0")
    + '[[examples]]\nname = "big 2"\ntext = "IMPORTANT: never guess."\n'
    + 'fires = { "bare" = "warn", "caps" = "warn" }\n',
    "wording/guidance/acme/acme-big-2/1.0.0.toml": head("1.0.0")
    + 'summary = "Big 2 reads every word."\ninstructions = ["Keep rules short."]\n'
    + 'sources = ["the acme docs"]\n',
    "output/json/default/1.0.0.toml": head("1.0.0")
    + definition("JSON in prose", "only valid json", "warn", "Use structured outputs."),
    "output/json/default/1.0.0.cases.toml": cases(["Output only valid JSON."], ["Reply."]),
}


def write_core(tmp_path: Path, changes: dict[str, str | None] | None = None) -> Path:
    """Write the core above, with some files changed (or, for None, left out)."""
    folder = tmp_path / "core"
    for name, body in {**CORE, **(changes or {})}.items():
        if body is None:
            continue
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (folder / "README.md").write_text("not a category", encoding="utf-8")
    (folder / "wording" / "bare" / "default" / "notes.md").write_text("-", encoding="utf-8")
    return folder


def ids(pack: RulePack) -> list[str]:
    return [rule.id for rule in pack.rules]


def rule_of(pack: RulePack, rule_id: str) -> Rule:
    return next(rule for rule in pack.rules if rule.id == rule_id)


def test_the_folders_are_listed_and_ordered(tmp_path: Path) -> None:
    folder = write_core(tmp_path)
    assert core_categories(folder) == ("wording", "output")
    assert core_rules("wording", folder) == ("acme-jargon", "bare", "caps", "hedge")
    assert core_targets("wording", folder) == ("default", "acme/default", "acme/acme-big-2")
    assert core_versions("wording", "bare", folder=folder) == ("1.0.0", "2.0.0")
    assert core_versions("wording", "examples", "acme/default", folder) == ("1.0.0",)
    releases = core_releases("wording", folder)
    assert [(r.version, r.released) for r in releases] == [
        ("1.0.0", "2026-10-07"),
        ("1.1.0", "2026-11-01"),
        ("2.0.0", "2026-12-01"),
    ]
    assert releases[2].changes == (("Changed in 2.0.0.", ("bare default", "hedge default")),)
    proved = verify_core(folder=folder)
    assert [(c, t) for c, t, _, _ in proved] == [
        ("wording", "default"),
        ("wording", "acme/default"),
        ("wording", "acme/acme-big-2"),
        ("output", "default"),
    ]
    assert all(check.passed for *_, checks in proved for check in checks)
    only = verify_core(targets=["acme/default"], folder=folder)
    assert [(c, t) for c, t, _, _ in only] == [("wording", "acme/default")]


def test_each_rule_is_read_along_the_model_s_chain(tmp_path: Path) -> None:
    folder = write_core(tmp_path)
    big = default_rules(("acme", "acme-big-2"), folder=folder)
    bare = rule_of(big, "wording/bare")
    assert bare.fix.endswith("Big 2 generalises from it.")
    assert bare.fires == ("Never guess.", "Always cite.", "Never round.")  # cases add up
    assert bare.origin == ("default 2.0.0", "acme/acme-big-2 1.0.0 extend")
    assert big.version == "2.0.0 (output 1.0.0) for acme:acme-big-2"
    assert ids(big) == ["wording/acme-jargon", "wording/bare", "wording/caps", "output/json"]
    small = default_rules(("acme", "acme-small"), folder=folder)
    other = default_rules(("zeta", "z-1"), folder=folder)
    assert "wording/acme-jargon" in ids(small)
    assert "wording/acme-jargon" not in ids(other)  # only acme models have it
    loud = "IMPORTANT: brief."
    assert [f.severity for f in lint_text(loud, big)] == ["warn"]
    assert [f.severity for f in lint_text(loud, small)] == ["info"]
    assert [f.severity for f in lint_text(loud, other)] == ["info"]
    assert {(p.name, p.target) for p in big.parts} >= {
        ("bare", "default"),
        ("bare", "acme/acme-big-2"),
        ("caps", "acme/default"),
        ("guidance", "acme/acme-big-2"),
    }


def test_severity_in_an_extension_reads_as_written(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {
            "wording/bare/acme/default/1.0.0.toml": head("1.0.0")
            + 'extend = true\nseverity = "error"\nmodels = ["acme-big*"]\n',
        },
    )
    bare = rule_of(default_rules(("acme", "acme-small"), folder=folder), "wording/bare")
    # With models: those models get it, every other one keeps the warning it had.
    assert (bare.severity_for("acme-big-1"), bare.severity_for("acme-small")) == ("error", "warn")
    project = tmp_path / "rules"
    write_project(
        project, {"wording/caps/default/1.0.0.toml": 'extend = true\nseverity = "error"\n'}
    )
    caps = rule_of(
        load_rules(project, target=("acme", "acme-small"), folder=folder), "wording/caps"
    )
    # Severity alone: every model, whatever models the rule named before.
    assert (caps.severity_for("acme-big-1"), caps.severity_for("acme-small")) == ("error", "error")


def test_guidance_comes_from_the_target_that_says_it(tmp_path: Path) -> None:
    folder = write_core(tmp_path)
    (note,) = default_rules(("acme", "acme-big-2"), folder=folder).guidance
    assert (note.category, note.target, note.version) == ("wording", "acme/acme-big-2", "1.0.0")
    assert note.instructions == ("Keep rules short.",)
    assert note.sources == ("the acme docs",)
    assert default_rules(("acme", "acme-small"), folder=folder).guidance == ()


# ---------------------------------------------------------------- releases


def test_a_pin_reads_every_file_as_it_was_at_that_release(tmp_path: Path) -> None:
    folder = write_core(tmp_path)

    def at(pin: str) -> RulePack:
        return default_rules(pins={"wording": pin}, folder=folder).only(["wording"])

    assert at("latest").version == "2.0.0"
    assert rule_of(at("latest"), "wording/bare").pattern == r"\b(never|always)\b"
    assert "wording/hedge" not in ids(at("latest"))  # removed at 2.0.0
    assert at("1").version == "1.1.0"  # the newest 1.x release
    assert rule_of(at("1"), "wording/bare").pattern == r"\bnever\b"
    assert "wording/hedge" in ids(at("1"))
    assert "wording/hedge" not in ids(at("1.0"))  # it did not exist yet
    assert at("1.0.0").version == "1.0.0"
    assert rule_of(at("2"), "wording/caps").origin == ("default 1.0.0",)  # untouched since 1.0.0


@pytest.mark.parametrize("pin", ["3", "1.5", "1.0.1", "0"])
def test_a_pin_must_name_a_release(tmp_path: Path, pin: str) -> None:
    folder = write_core(tmp_path)
    with pytest.raises(RuleError) as caught:
        default_rules(pins={"wording": pin}, folder=folder)
    assert caught.value.problems == [
        f"wording has no release matching {pin!r}; it has 1.0.0, 1.1.0, 2.0.0"
    ]


def test_a_release_s_files_share_a_day(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {"wording/caps/default/2.0.0.toml": 'released = "2026-12-02"\nchanges = ["x"]\n'},
    )
    with pytest.raises(RuleError, match=re.escape("disagree on the day: 2026-12-01, 2026-12-02")):
        core_releases("wording", folder)


@pytest.mark.parametrize(
    ("pin", "valid"),
    [
        ("latest", True),
        ("1", True),
        ("1.2", True),
        ("1.2.3", True),
        ("v1", False),
        ("1.2.3.4", False),
        ("", False),
    ],
)
def test_what_counts_as_a_pin(pin: str, valid: bool) -> None:
    assert is_pin(pin) is valid


# ------------------------------------------------------------ core files

BARE = "wording/bare/default/2.0.0.toml"
BARE_CASES = "wording/bare/default/2.0.0.cases.toml"
CAPS_ACME = "wording/caps/acme/default/1.0.0.toml"


@pytest.mark.parametrize(
    ("changes", "source", "problem"),
    [
        ({BARE: CORE[BARE].replace('released = "2026-12-01"\n', "")}, BARE, "released: missing"),
        (
            {BARE: CORE[BARE].replace('changes = ["Changed in 2.0.0."]', "changes = []")},
            BARE,
            "changes: say what changed",
        ),
        ({BARE: CORE[BARE].replace('title = "A bare rule"\n', "")}, BARE, "title: missing"),
        ({BARE: CORE[BARE] + 'fx = "y"\n'}, BARE, "fx: no such field - did you mean 'fix'?"),
        (
            {BARE: CORE[BARE] + 'models = ["acme-big*"]\n'},
            BARE,
            "models: a default file is for every model",
        ),
        (
            {BARE: CORE[BARE].replace("title", "extend = true\ntitle")},
            BARE,
            "extend: a default file defines its rule",
        ),
        (
            {BARE: head("2.0.0") + "from = '1'\n"},
            BARE,
            "from: only a project's rules can fall back",
        ),
        ({BARE: CORE[BARE] + "[[nope\n"}, BARE, "not valid TOML"),
        (
            {BARE: CORE[BARE].replace("never|always", "(")},
            BARE,
            "pattern: not a valid regular expression",
        ),
        ({BARE: CORE[BARE] + "run = 3\nat_least = 2\n"}, BARE, "choose one of run, at_least"),
        (
            {BARE_CASES: None},
            BARE,
            "cases: every rule needs text it fires on and text it stays quiet on",
        ),
        (
            {BARE_CASES: CORE[BARE_CASES] + "fire = []\n"},
            BARE,
            "cases.fire: no such field - did you mean 'fires'?",
        ),
        (
            {CAPS_ACME: head("1.0.0") + "enabled = true\n"},
            CAPS_ACME,
            "enabled: only false means something",
        ),
        (
            {CAPS_ACME: head("1.0.0") + "enabled = false\nfix = 'x'\n"},
            CAPS_ACME,
            "a `enabled` rule takes only enabled, not fix",
        ),
        (
            {CAPS_ACME: head("1.0.0") + "extend = true\nenabled = false\n"},
            CAPS_ACME,
            "choose one of enabled, extend",
        ),
        (
            {CAPS_ACME: head("1.0.0") + "extend = false\n"},
            CAPS_ACME,
            "extend: give true, or leave it out",
        ),
        (
            {CAPS_ACME: head("1.0.0") + "extend = true\npatern = 'x'\n"},
            CAPS_ACME,
            "patern: no such field - did you mean 'pattern'?",
        ),
        (
            {"wording/acme-jargon/acme/default/1.0.0.toml": head("1.0.0") + "extend = true\n"},
            "wording/acme-jargon/acme/default/1.0.0.toml",
            "extend: nothing to extend: it is not defined below this file",
        ),
        (
            {"wording/hedge/acme/default/2.0.0.toml": head("2.0.0") + "extend = true\n"},
            "wording/hedge/acme/default/2.0.0.toml",
            "extend: nothing to extend: wording 2.0.0 retired it",
        ),
        (
            {"wording/bare/acme/default/1.0.0.toml": head("1.0.0") + "enabled = false\n"},
            "wording/bare/acme/acme-big-2/1.0.0.toml",
            "extend: nothing to extend: it was turned off below this file",
        ),
        (
            {"wording/hedge/default/2.0.0.cases.toml": cases(["x"], ["y"])},
            "wording/hedge/default/2.0.0.toml",
            "cases: a `enabled` rule has no cases of its own",
        ),
        (
            {"wording/guidance/acme/acme-big-2/1.0.0.toml": head("1.0.0") + 'summary = "x"\n'},
            "wording/guidance/acme/acme-big-2/1.0.0.toml",
            "instructions: give at least one",
        ),
        (
            {
                "wording/guidance/acme/acme-big-2/1.0.0.toml": head("1.0.0")
                + 'instructions = ["x"]\nsumary = "x"\n'
            },
            "wording/guidance/acme/acme-big-2/1.0.0.toml",
            "sumary: no such field - did you mean 'summary'?",
        ),
        (
            {
                "wording/examples/default/1.0.0.toml": CORE[
                    "wording/examples/default/1.0.0.toml"
                ].replace('"bare" = "warn"', '"bare" = "loud"')
            },
            "wording/examples/default/1.0.0.toml",
            "examples[0].fires.bare: 'loud' is not one of",
        ),
        (
            {
                "wording/examples/default/1.0.0.toml": CORE[
                    "wording/examples/default/1.0.0.toml"
                ].replace('"bare" =', '"nope" =')
            },
            "wording/examples/default/1.0.0.toml",
            "examples[0].fires: no rule 'wording/nope'",
        ),
        (
            {"wording/examples/default/1.0.0.toml": head("1.0.0") + "examples = 1\n"},
            "wording/examples/default/1.0.0.toml",
            "examples: expected [[examples]] tables",
        ),
        (
            {
                "wording/examples/default/1.0.0.toml": head("1.0.0")
                + '[[examples]]\nname = "e"\ntext = "t"\nfires = 3\n'
            },
            "wording/examples/default/1.0.0.toml",
            "examples[0].fires: expected a list",
        ),
        (
            {
                "wording/examples/default/1.0.0.toml": head("1.0.0")
                + '[[examples]]\nname = "e"\ntext = "t"\ncounts = 1\n'
            },
            "wording/examples/default/1.0.0.toml",
            "examples[0].counts: expected a table",
        ),
        ({"wording/W_bad/default/1.0.0.toml": head("1.0.0")}, "wording/W_bad", "not a rule name"),
        ({"wording/empty/notes.toml": ""}, "wording/empty", "no target folders"),
    ],
)
def test_a_core_file_is_checked_where_it_sits(
    tmp_path: Path, changes: dict[str, str | None], source: str, problem: str
) -> None:
    folder = write_core(tmp_path, changes)
    with pytest.raises(RuleError) as caught:
        default_rules(("acme", "acme-big-2"), folder=folder)
    assert str(caught.value).startswith(f"{source} has"), str(caught.value)
    assert any(problem in line for line in caught.value.problems), caught.value.problems


def test_a_rewrite_that_breaks_its_cases_fails_its_proof(tmp_path: Path) -> None:
    folder = write_core(tmp_path, {CAPS_ACME: head("1.0.0") + "extend = true\npattern = 'zzz'\n"})
    pack = default_rules(("acme", "acme-small"), folder=folder)
    caps = next(c for c in verify_rules(pack) if c.name == "wording/caps")
    assert caps.failures == ("should fire on: 'IMPORTANT: be brief.'",)


def test_a_file_drops_the_examples_it_makes_untrue(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {
            CAPS_ACME: head("1.0.0") + 'extend = true\nseverity = "error"\n',
            "wording/bare/acme/default/1.0.0.toml": head("1.0.0") + "enabled = false\n",
            "wording/examples/acme/default/1.0.0.toml": None,
        },
    )
    base = default_rules(pins={"wording": "1.0"}, folder=folder)
    assert [example.name for example in base.examples] == ["loud"]
    over = default_rules(("acme", "acme-small"), pins={"wording": "1.0"}, folder=folder)
    # "loud" named wording/bare, now off, and wording/caps's severity, now changed: it goes.
    assert over.examples == ()
    assert "wording/bare" not in ids(over)
    assert all(check.passed for check in verify_rules(over))


def test_an_example_that_misfires_is_explained(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {
            "wording/examples/acme/default/1.0.0.toml": head("1.0.0")
            + '[[examples]]\nname = "wrong"\nmodel = "acme-small"\n'
            + 'text = "Never guess. Never round. IMPORTANT."\n'
            + 'fires = { "bare" = "error", "hedge" = "info" }\ncounts = { "bare" = 1 }\n'
        },
    )
    pack = default_rules(("acme", "acme-small"), pins={"wording": "1.1"}, folder=folder)
    check = next(c for c in verify_rules(pack) if c.name == "wrong")
    assert check.failures == (
        "wording/hedge should fire",
        "wording/caps fired but should not",
        "wording/bare should fire 1 times, fired 2",
        "wording/bare should be error, was warn",
    )


# ------------------------------------------------------------- the project


def write_project(folder: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return folder


SORRY = {
    "brand/sorry/default/1.0.0.toml": 'pattern = "apologi[sz]e"\nfix = "Drop the apology."\n',
    "brand/sorry/default/1.0.0.cases.toml": 'fires = ["Apologise once."]\nquiet = ["Say what happened."]\n',
}


@pytest.mark.parametrize(
    ("files", "problem"),
    [
        (
            {"brand/sorry/default/1.0.0.toml": 'fix = "x"\n'},
            "rules/brand/sorry/default/1.0.0.toml: pattern: missing",
        ),
        (
            {"brand/sorry/default/1.0.0.cases.toml": 'fires = ["x"]\n'},
            "rules/brand/sorry/default/1.0.0.toml: cases: every rule needs text it fires on",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + 'fx = "y"\n'
            },
            "fx: no such field - did you mean 'fix'?",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + 'severity = "fatal"\n'
            },
            "severity: 'fatal' is not one of",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + 'scope = ["docs"]\n'
            },
            "scope: 'docs' is not one of",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + 'run = 3\nunless = "x"\n'
            },
            "unless: only applies to a rule that fires per hit",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + "run = -1\n"
            },
            "run: expected a whole number, 0 or more",
        ),
        (
            {
                "brand/sorry/default/1.0.0.toml": SORRY["brand/sorry/default/1.0.0.toml"]
                + 'missing = "yes"\n'
            },
            "missing: expected true or false",
        ),
        (
            {"brand/sorry/default/1.0.0.toml": "[[nope\n"},
            "rules/brand/sorry/default/1.0.0.toml: not valid TOML",
        ),
        (
            {"brand/sorry/notes.toml": ""},
            "rules/brand/sorry/notes.toml: a file here is never read; put it in rules/brand/sorry/default/",
        ),
        (
            {"brand/sorry/default/1.0.0.toml": None},
            "rules/brand/sorry/default/1.0.0.cases.toml: cases without their rule, 1.0.0.toml",
        ),
        (
            {"brand/sorry/default/1.toml": ""},
            "rules/brand/sorry/default/1.toml: name it for its version",
        ),
        (
            {"brand/sorry/anthropic/1.0.0.toml": ""},
            "rules/brand/sorry/anthropic/1.0.0.toml: a file here is never read",
        ),
        ({"brand/empty/notes.md": ""}, "rules/brand/empty: no target folders"),
        ({"brand/Bad_Name/default/1.0.0.toml": ""}, "rules/brand/Bad_Name: not a rule name"),
        (
            {"brand/stray.toml": ""},
            "rules/brand/stray.toml: a rule's files go in rules/brand/<rule>/default/",
        ),
        (
            {"stray.toml": ""},
            "rules/stray.toml: rules go in rules/<category>/<rule>/default/1.0.0.toml",
        ),
        (
            {"context/show-reasoning/default/1.0.0.toml": "enabled = false\n"},
            "did you mean reasoning/show-reasoning? Its folder is rules/reasoning/show-reasoning/",
        ),
        (
            {
                "wording/hedged-requirement/default/1.0.0.toml": "enabled = false\n",
                "wording/hedged-requirement/default/1.0.0.cases.toml": "",
            },
            "cases: a `enabled` rule has no cases of its own",
        ),
        (
            {"wording/hedged-requirement/default/1.0.0.toml": "extend = true\nseverity = 'loud'\n"},
            "rules/wording/hedged-requirement/default/1.0.0.toml: severity: 'loud'",
        ),
        (
            {"wording/rule-without-reason/default/1.0.0.toml": "from = 'one'\n"},
            'from: expected a release, as in "1" or "1.0.0"',
        ),
        (
            {"wording/rule-without-reason/default/1.0.0.toml": "from = 'latest'\n"},
            "from: expected a release",
        ),
        (
            {"wording/rule-without-reason/default/1.0.0.toml": "from = '7'\n"},
            "from: wording has no release matching '7'; it has 1.0.0",
        ),
        (
            {"wording/rule-without-reason/default/1.0.0.toml": "from = '1'\npattern = 'x'\n"},
            "a `from` rule takes only from, not pattern",
        ),
        (
            {"brand/sorry/default/1.0.0.toml": "from = '1'\n"},
            "from: brand/sorry is not a core rule",
        ),
        (
            {"wording/no-such-rule/default/1.0.0.toml": "enabled = false\n"},
            "enabled: no rule wording/no-such-rule to turn off here",
        ),
        (
            {
                "brand/examples/default/1.0.0.toml": "[[examples]]\nname = 'e'\ntext = 't'\nfires = ['NOPE']\n"
            },
            "rules/brand/examples/default/1.0.0.toml: examples[0].fires: no rule 'brand/NOPE'",
        ),
        (
            {"brand/examples/default/1.0.0.toml": "[[nope\n"},
            "rules/brand/examples/default/1.0.0.toml: not valid TOML",
        ),
        (
            {"brand/examples/default/1.0.0.toml": "rules = 1\n"},
            "rules/brand/examples/default/1.0.0.toml: rules: no such field",
        ),
    ],
)
def test_a_broken_project_folder_names_every_problem(
    tmp_path: Path, files: dict[str, str | None], problem: str
) -> None:
    merged = {**SORRY, **files}
    folder = write_project(tmp_path / "rules", {k: v for k, v in merged.items() if v is not None})
    with pytest.raises(RuleError) as caught:
        load_rules(folder)
    assert str(caught.value).startswith("rules/ has")
    assert any(problem in line for line in caught.value.problems), caught.value.problems


def test_the_project_folder_must_be_a_folder(tmp_path: Path) -> None:
    (tmp_path / "rules").write_text("", encoding="utf-8")
    with pytest.raises(RuleError, match="expected a folder"):
        load_rules(tmp_path / "rules")


def test_a_project_adds_extends_replaces_and_turns_off(tmp_path: Path) -> None:
    folder = write_project(
        tmp_path / "rules",
        {
            **SORRY,
            "brand/examples/default/1.0.0.toml": '[[examples]]\nname = "sorry"\ntext = "Apologise first."\n'
            'fires = { "sorry" = "warn" }\n',
            "brand/README.md": "notes are fine",
            "wording/hedged-requirement/default/1.0.0.toml": "enabled = false\n",
            "context/restated-default/default/1.0.0.toml": 'pattern = "be helpful"\nfix = "Say what helpful means."\n',
            "context/restated-default/default/1.0.0.cases.toml": 'fires = ["Be helpful."]\nquiet = ["Be clear."]\n',
            "wording/capitals/default/1.0.0.toml": 'extend = true\nseverity = "error"\n',
            "wording/capitals/default/1.0.0.cases.toml": 'quiet = ["NASA approved it."]\n',
            "wording/mine/default/1.0.0.toml": 'title = "Mine"\npattern = "mine"\nfix = "Not mine."\n',
            "wording/mine/default/1.0.0.cases.toml": 'fires = ["mine"]\nquiet = ["yours"]\n',
        },
    )
    pack = load_rules(folder, target=("anthropic", "claude-opus-5-5"))
    names = ids(pack)
    assert "wording/hedged-requirement" not in names
    assert pack.categories[-1] == "brand"  # a category the core does not have comes last
    assert names[-1] == "brand/sorry"
    wording = [rule.id for rule in pack.rules if rule.category == "wording"]
    assert wording[-1] == "wording/mine"  # a new rule joins its category, after the core's
    virtue = rule_of(pack, "context/restated-default")
    assert (virtue.pattern, virtue.category) == ("be helpful", "context")
    assert virtue.origin == ("default 1.0.0", "rules/default 1.0.0 replace")
    caps = rule_of(pack, "wording/capitals")
    assert caps.severity_for("claude-haiku-4-5") == "error"
    assert "NASA approved it." in caps.quiet
    assert len(caps.quiet) > 1  # the core cases are kept
    assert caps.origin == (
        "default 1.0.0",
        "anthropic/default 1.0.0 extend",
        "rules/default 1.0.0 extend",
    )
    assert pack.version == "1.0.0 for anthropic:claude-opus-5-5 + rules/"
    checks = verify_rules(pack)
    assert [c for c in checks if not c.passed] == []
    examples = {example.name for example in pack.examples}
    assert "sorry" in examples
    assert "wording, before" not in examples  # it named the disabled wording/hedged-requirement
    assert "layout, before" not in examples  # and this one the replaced context/restated-default
    narrowed = load_rules(folder, categories=["brand"])
    assert (narrowed.categories, ids(narrowed), narrowed.version) == (
        ("brand",),
        ["brand/sorry"],
        "rules/",
    )


def test_one_rule_falls_back_along_its_whole_chain(tmp_path: Path) -> None:
    core = write_core(tmp_path)
    project = write_project(
        tmp_path / "rules",
        {
            "wording/bare/default/1.0.0.toml": "from = '1'\n",
            "wording/hedge/default/1.0.0.toml": "from = '1.1'\n",
        },
    )
    pack = load_rules(project, target=("acme", "acme-big-2"), folder=core)
    assert pack.version == "2.0.0 (output 1.0.0) for acme:acme-big-2 + rules/"
    bare = rule_of(pack, "wording/bare")
    assert bare.pattern == r"\bnever\b"  # as 1.x had it; 2.0.0 also catches "always"
    assert bare.fix.endswith("Big 2 generalises from it.")  # its model file comes along
    assert bare.origin == (
        "default 1.0.0",
        "acme/acme-big-2 1.0.0 extend",
        "rules/default 1.0.0 from 1",
    )
    assert "wording/hedge" in ids(pack)  # removed at 2.0.0, back as 1.1.0 had it
    assert all(check.passed for check in verify_rules(pack))
    pinned = load_rules(project, pins={"wording": "1"}, folder=core)
    assert pinned.version == "1.1.0 (output 1.0.0) + rules/"
    early = write_project(
        tmp_path / "early", {"wording/hedge/default/1.0.0.toml": "from = '1.0'\n"}
    )
    with pytest.raises(RuleError, match=re.escape("from: wording 1.0.0 has no rule wording/hedge")):
        load_rules(early, folder=core)


def test_a_project_has_vendor_and_model_files_like_the_core(tmp_path: Path) -> None:
    core = write_core(tmp_path)
    project = write_project(
        tmp_path / "rules",
        {
            # Every model: a warning, with one more phrasing it must stay quiet on.
            "wording/caps/default/1.0.0.toml": 'extend = true\nseverity = "warn"\n',
            "wording/caps/default/1.0.0.cases.toml": 'quiet = ["Important: be brief."]\n',
            # One model: an error, and a wider pattern with a case only it must pass.
            "wording/caps/acme/acme-big-2/1.0.0.toml": "extend = true\nseverity = 'error'\n"
            "pattern = '\\b(IMPORTANT|CRITICAL)\\b'\n",
            "wording/caps/acme/acme-big-2/1.0.0.cases.toml": 'fires = ["CRITICAL: be brief."]\n',
            # A newer file takes the older one's place.
            "wording/bare/acme/default/1.0.0.toml": 'extend = true\nseverity = "info"\n',
            "wording/bare/acme/default/1.1.0.toml": 'released = "2026-11-02"\nchanges = ["Back to a warning."]\n'
            'extend = true\nseverity = "warn"\n',
        },
    )
    big = load_rules(project, target=("acme", "acme-big-2"), folder=core)
    small = load_rules(project, target=("acme", "acme-small"), folder=core)
    assert rule_of(big, "wording/caps").origin == (
        "default 1.0.0",
        "acme/default 1.0.0 extend",
        "rules/default 1.0.0 extend",
        "rules/acme/acme-big-2 1.0.0 extend",
    )
    assert rule_of(big, "wording/caps").severity_for("acme-big-2") == "error"
    assert rule_of(small, "wording/caps").severity_for("acme-small") == "warn"
    assert "CRITICAL: be brief." in rule_of(big, "wording/caps").fires
    assert "CRITICAL: be brief." not in rule_of(small, "wording/caps").fires
    assert rule_of(small, "wording/bare").origin[-1] == "rules/acme/default 1.1.0 extend"
    assert all(check.passed for check in verify_rules(big))
    assert all(check.passed for check in verify_rules(small))
    proved = verify_project(project, folder=core)
    assert [target for target, _, _ in proved] == ["default", "acme/default", "acme/acme-big-2"]
    assert all(check.passed for _, _, checks in proved for check in checks)


def test_a_project_case_is_proved_on_the_target_it_sits_in(tmp_path: Path) -> None:
    core = write_core(tmp_path)
    project = write_project(
        tmp_path / "rules",
        {
            # This case needs the wider pattern, but sits in the default folder.
            "wording/caps/default/1.0.0.toml": "extend = true\n",
            "wording/caps/default/1.0.0.cases.toml": 'fires = ["CRITICAL: be brief."]\n',
            "wording/caps/acme/acme-big-2/1.0.0.toml": "extend = true\n"
            "pattern = '\\b(IMPORTANT|CRITICAL)\\b'\n",
        },
    )
    proved = {target: checks for target, _, checks in verify_project(project, folder=core)}
    caps = next(c for c in proved["default"] if c.name == "wording/caps")
    assert caps.failures == ("should fire on: 'CRITICAL: be brief.'",)
    assert all(check.passed for check in proved["acme/acme-big-2"])


# ------------------------------------------------- retiring, and files released later

RETIRE_CAPS: dict[str, str | None] = {
    "wording/caps/default/2.0.0.toml": head("2.0.0") + "enabled = false\n"
}


def test_a_retired_rule_leaves_nothing_behind(tmp_path: Path) -> None:
    """Retiring a rule is one default file: older files about it are moot, not errors."""
    folder = write_core(tmp_path, RETIRE_CAPS)
    for target in (None, ("acme", "acme-small"), ("acme", "acme-big-2")):
        pack = default_rules(target, folder=folder)
        assert "wording/caps" not in ids(pack)  # acme's 1.0.0 extension of it is skipped
        assert all("wording/caps" not in example.fires for example in pack.examples)
        assert all(check.passed for check in verify_rules(pack))
    before = default_rules(("acme", "acme-small"), pins={"wording": "1"}, folder=folder)
    assert rule_of(before, "wording/caps").severity_for("acme-big-1") == "warn"
    releases = core_releases("wording", folder)
    assert ("Changed in 2.0.0.", ("bare default", "caps default", "hedge default")) in (
        releases[-1].changes
    )


def test_a_file_written_after_a_retirement_still_has_to_make_sense(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {
            **RETIRE_CAPS,
            "wording/caps/acme/default/2.0.0.toml": head("2.0.0")
            + 'extend = true\nseverity = "error"\n',
        },
    )
    with pytest.raises(RuleError) as caught:
        default_rules(("acme", "acme-small"), folder=folder)
    assert caught.value.problems == [
        "extend: nothing to extend: wording 2.0.0 retired it; remove this file,"
        " or pin lint.rules.wording to a release before 2.0.0"
    ]
    project = write_project(
        tmp_path / "rules",
        {"wording/caps/default/1.0.0.toml": 'extend = true\nseverity = "error"\n'},
    )
    with pytest.raises(RuleError, match=r"wording 2\.0\.0 retired it; remove this file"):
        load_rules(project, folder=write_core(tmp_path / "again", RETIRE_CAPS))


def test_a_file_released_later_is_absent_under_an_older_pin(tmp_path: Path) -> None:
    folder = write_core(
        tmp_path,
        {
            "wording/bare/acme/acme-big-2/1.0.0.toml": None,
            "wording/bare/acme/acme-big-2/1.0.0.cases.toml": None,
            "wording/bare/acme/acme-big-2/1.1.0.toml": head("1.1.0")
            + 'extend = true\nseverity = "error"\n',
            "wording/guidance/acme/default/1.1.0.toml": head("1.1.0")
            + 'summary = "Acme reads literally."\ninstructions = ["Say exactly what you mean."]\n',
        },
    )

    def bare(pin: str) -> Rule:
        return rule_of(
            default_rules(("acme", "acme-big-2"), pins={"wording": pin}, folder=folder),
            "wording/bare",
        )

    assert (bare("1.0").origin, bare("1.0").severity_for("acme-big-2")) == (
        ("default 1.0.0",),
        "warn",
    )
    assert bare("1").origin == ("default 1.0.0", "acme/acme-big-2 1.1.0 extend")
    assert bare("1").severity_for("acme-big-2") == "error"
    assert bare("latest").origin == ("default 2.0.0", "acme/acme-big-2 1.1.0 extend")
    guided = {
        pin: [
            note.target
            for note in default_rules(
                ("acme", "acme-big-2"), pins={"wording": pin}, folder=folder
            ).guidance
        ]
        for pin in ("1.0", "1")
    }
    assert guided == {"1.0": ["acme/acme-big-2"], "1": ["acme/default", "acme/acme-big-2"]}


def test_a_rule_says_how_it_matches_and_how_severe_it_is_in_words() -> None:
    assert rule().describe_matching() == "Fires on every match."
    assert rule(unless="because").describe_matching().startswith("Fires on every match, unless")
    assert (
        rule(run=3).describe_matching() == "Fires once on 3 or more sentences in a row that match."
    )
    assert rule(run=2, unit="line").describe_matching().endswith("lines in a row that match.")
    assert (
        rule(at_least=2).describe_matching()
        == "Fires once when the pattern matches 2 or more times."
    )
    assert rule(missing=True).describe_matching().startswith("Fires when nothing matches")
    raised = rule(severity="error", models=("claude-opus-5*",), otherwise="warn")
    assert raised.describe_severity(None) == "error on claude-opus-5*; warn elsewhere"
    assert raised.describe_severity("claude-opus-5-5") == "error"
    assert rule(severity="info").describe_severity(None) == "info"
