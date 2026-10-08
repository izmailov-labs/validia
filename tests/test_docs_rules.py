"""The docs hook that writes the prompt rules pages: per target, one view per release."""

import importlib.util
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

HOOK = Path(__file__).parents[1] / "docs" / "_hooks" / "rules_catalog.py"


def head(version: str, day: str) -> str:
    return f'released = "{day}"\nchanges = ["Changed at {version}."]\n\n'


V1, V2 = head("1.0.0", "2026-01-01"), head("1.1.0", "2026-02-01")


CORE = {
    # wording/caps: info for everyone, a warning on the big acme models; at 1.1.0,
    # an error on acme-big alone.
    "wording/caps/default/1.0.0.toml": V1
    + 'title = "Capitals"\npattern = "IMPORTANT"\nfix = "Say it plainly."\n'
    + 'severity = "info"\ncase_sensitive = true\n',
    "wording/caps/default/1.0.0.cases.toml": 'fires = ["IMPORTANT: brief."]\nquiet = ["Brief."]\n',
    "wording/caps/acme/default/1.0.0.toml": V1
    + 'extend = true\nseverity = "warn"\nmodels = ["acme-big*"]\n',
    "wording/caps/acme/acme-big/1.1.0.toml": V2 + 'extend = true\nseverity = "error"\n',
    # wording/hedge: first released at 1.1.0.
    "wording/hedge/default/1.1.0.toml": V2
    + 'title = "A hedge"\npattern = "try to"\nfix = "Make it firm."\nseverity = "info"\n',
    "wording/hedge/default/1.1.0.cases.toml": 'fires = ["Try to help."]\nquiet = ["Help."]\n',
    # Whole prompts: one that fires, one that does not.
    "wording/examples/default/1.0.0.toml": V1
    + '[[examples]]\nname = "loud"\ntext = "IMPORTANT: brief.\\nIMPORTANT: kind."\n'
    + 'fires = ["caps"]\ncounts = { "caps" = 2 }\n\n'
    + '[[examples]]\nname = "plain"\ntext = "Be brief."\nfires = []\n',
}


EFFECTS = {
    "wording/caps": ("Capitals.", "The model overtriggers."),
    "wording/hedge": ("A hedge.", "The model reads it as optional."),
}


@pytest.fixture
def hook() -> ModuleType:
    if not HOOK.is_file():
        pytest.skip("the docs hook is in the repository, not in the sdist")
    spec = importlib.util.spec_from_file_location("rules_catalog", HOOK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def core(tmp_path: Path) -> Path:
    for name, body in CORE.items():
        path = tmp_path / "core" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path / "core"


def view(page: str, release: str) -> str:
    """The part of a page that shows one release."""
    start = page.index(f'data-release="{release}"')
    return page[start : page.index("</div>", start)]


def test_a_page_has_one_view_per_release_newest_first(hook: ModuleType, core: Path) -> None:
    page = hook.expand("<!-- rules-page: acme/acme-big -->", "rules/acme/acme-big.md", core)

    assert (
        '<option value="1.1.0" selected>1.1.0 (latest)</option><option value="1.0.0">1.0.0</option>'
    ) in page
    assert page.index('data-release="1.1.0"') < page.index('data-release="1.0.0"')
    assert 'data-release="1.0.0" hidden="hidden" data-search-exclude=""' in page
    assert 'data-release="1.1.0" markdown>' in page


def test_each_view_shows_the_rules_as_that_release_has_them(hook: ModuleType, core: Path) -> None:
    page = hook.expand("<!-- rules-page: acme/acme-big -->", "rules/acme/acme-big.md", core)
    newest, oldest = view(page, "1.1.0"), view(page, "1.0.0")

    assert "| `wording/caps` | Capitals | **error** (default info) |" in newest
    assert "| `wording/caps` | Capitals | **warn** (default info) |" in oldest
    assert "`wording/hedge`" in newest
    assert "`wording/hedge`" not in oldest
    assert 'a pin reads it as `wording = "1.0.0"`' in oldest


def test_a_provider_page_shows_only_what_its_files_change(hook: ModuleType, core: Path) -> None:
    page = hook.expand("<!-- rules-page: acme/default -->", "rules/acme/index.md", core)
    newest = view(page, "1.1.0")

    assert "| `wording/caps` | Capitals | info | warn on `acme-big*`; info on the rest |" in newest
    assert "`wording/hedge`" not in newest
    assert "[`acme/acme-big`](acme-big.md)" in page


def test_links_are_relative_to_the_page(hook: ModuleType, core: Path) -> None:
    default = hook.expand("<!-- rules-page: default -->", "rules/default.md", core)
    model = hook.expand("<!-- rules-page: acme/acme-big -->", "rules/acme/acme-big.md", core)

    assert "[`acme/acme-big`](acme/acme-big.md): error" in view(default, "1.1.0")
    assert "[`default`](../default.md)" in model


def test_the_release_history_lists_every_release(hook: ModuleType, core: Path) -> None:
    page = hook.expand("<!-- rules-releases -->", "rules/releases.md", core)

    assert page.index("**1.1.0** (latest), released 2026-02-01") < page.index(
        "**1.0.0**, released 2026-01-01"
    )
    assert "- Changed at 1.1.0. (`wording/caps acme/acme-big`, `wording/hedge default`)" in page


def test_an_unknown_target_is_a_warning(
    hook: ModuleType, core: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        page = hook.expand("<!-- rules-page: acme/nope -->", "rules/acme/nope.md", core)

    assert page == ""
    assert "'acme/nope', which is not a target of the rules" in caplog.text


def test_a_rule_example_has_cause_effect_negative_and_positive(
    hook: ModuleType, core: Path
) -> None:
    page = view(
        hook.expand("<!-- rules-page: default -->", "rules/default.md", core, EFFECTS), "1.1.0"
    )
    example = page.split('??? example "wording/caps: Capitals"')[1].split("???")[0]

    assert "**Cause:** Capitals." in example
    assert "**Effect:** The model overtriggers." in example
    assert "✗ **Negative:** `lint` flags it as **info**." in example
    assert "- <code>IMPORTANT: brief.</code> flags <code>IMPORTANT</code>" in example
    assert "✓ **Positive:** `lint` stays quiet." in example
    assert "- <code>Brief.</code>" in example
    assert "**Fix:** Say it plainly." in example


def test_a_model_page_gives_examples_for_the_rules_it_changes(hook: ModuleType, core: Path) -> None:
    page = hook.expand(
        "<!-- rules-page: acme/acme-big -->", "rules/acme/acme-big.md", core, EFFECTS
    )
    newest = view(page, "1.1.0")

    assert "✗ **Negative:** `lint` flags it as **error** on this model." in newest
    assert '??? example "wording/hedge' not in newest
    assert "Examples for every other rule are on the [`default`](../default.md) page." in newest


def test_whole_prompt_examples_say_what_fires(hook: ModuleType, core: Path) -> None:
    page = view(
        hook.expand("<!-- rules-page: default -->", "rules/default.md", core, EFFECTS), "1.1.0"
    )

    assert '??? failure "Whole prompt: loud"' in page
    assert "    IMPORTANT: brief.\n    IMPORTANT: kind." in page
    assert "✗ `lint` flags `wording/caps` 2 times." in page
    assert '??? success "Whole prompt: plain"' in page
    assert "✓ `lint` flags nothing." in page


def test_every_rule_needs_a_cause_and_effect(hook: ModuleType, core: Path) -> None:
    catalog = hook._catalog(core, {"wording/caps": ("A cause.", "An effect.")})

    assert catalog.rule_ids() - set(catalog.effects) == {"wording/hedge"}


def test_a_target_without_a_page_is_a_warning(
    hook: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    # The core rules and docs/rules/effects.toml as they ship: a warning about a missing
    # cause and effect would show up here too.
    pages = [hook.page_of(target) for target in hook._catalog(None).targets]
    files = [SimpleNamespace(src_uri=page) for page in pages if page != "rules/default.md"]

    with caplog.at_level(logging.WARNING):
        hook.on_files(files)

    assert caplog.messages == [
        "the rules have files for default, but the docs have no rules/default.md"
    ]
