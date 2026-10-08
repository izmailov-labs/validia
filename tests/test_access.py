"""Model access: provider inference, and key lookup in franca's own order."""

from pathlib import Path

import pytest
from franca.core.settings import ProviderSettings, SettingsKeyProvider
from franca.core.settings import Settings as FrancaSettings
from pydantic import SecretStr

from validia.runs.access import (
    KNOWN_PROVIDERS,
    AccessError,
    KeyEnvironment,
    ModelSpec,
    key_source,
    key_variables,
    parse_model,
)


@pytest.mark.parametrize(
    ("spec", "provider", "name"),
    [
        ("anthropic:claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5"),
        ("claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5"),
        ("gpt-5", "openai", "gpt-5"),
        ("gemini-3-pro", "google", "gemini-3-pro"),
        ("grok-5", "xai", "grok-5"),
        ("deepseek-v4", "deepseek", "deepseek-v4"),
        ("my_shim:llama-4", "my_shim", "llama-4"),
    ],
)
def test_a_model_names_its_provider(spec: str, provider: str, name: str) -> None:
    parsed = parse_model(spec)
    assert parsed == ModelSpec(provider, name)
    assert str(parsed) == f"{provider}:{name}"


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        ("llama-4", "cannot tell which provider serves 'llama-4'"),
        (":claude", "needs both halves"),
        ("anthropic:", "needs both halves"),
    ],
)
def test_an_unclear_model_is_refused(spec: str, problem: str) -> None:
    with pytest.raises(AccessError, match=problem):
        parse_model(spec)


def test_key_variables_are_listed_in_lookup_order() -> None:
    providers = {"anthropic": ProviderSettings(api_key_env="TEAM_KEY")}
    assert key_variables("anthropic", providers) == [
        "TEAM_KEY",
        "FRANCA_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
    ]
    assert key_variables("my_shim", {}) == ["FRANCA_MY_SHIM_API_KEY"]


ENVIRONMENTS = [
    {},
    {"ANTHROPIC_API_KEY": "a"},
    {"ANTHROPIC_API_KEY": "a", "FRANCA_ANTHROPIC_API_KEY": "f"},
    {"ANTHROPIC_API_KEY": "a", "TEAM_KEY": "t"},
    {"ANTHROPIC_API_KEY": ""},
]


@pytest.mark.parametrize("environ", ENVIRONMENTS)
@pytest.mark.parametrize(
    "configured",
    [
        {},
        {"anthropic": ProviderSettings(api_key_env="TEAM_KEY")},
        {"anthropic": ProviderSettings(api_key=SecretStr("in-file"))},
    ],
)
def test_key_source_agrees_with_franca(
    environ: dict[str, str], configured: dict[str, ProviderSettings]
) -> None:
    """Where validia says the key comes from is where franca will take it from."""
    source = key_source("anthropic", configured, environ)
    franca = SettingsKeyProvider(FrancaSettings(providers=configured), environ)  # type: ignore[arg-type]
    found = franca.key_for("anthropic")  # type: ignore[arg-type]
    assert (source is None) == (found is None)
    if source is not None and found is not None and source in environ:
        assert found.get_secret_value() == environ[source]


# ------------------------------------------------------------ the environment


def keyfile(tmp_path: Path, name: str, text: str) -> Path:
    """A dotenv-format file of fake keys."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_known_providers_are_franca_s() -> None:
    assert KNOWN_PROVIDERS == ("anthropic", "openai", "google", "xai", "deepseek")


def test_a_key_environment_reads_files_under_the_process(tmp_path: Path) -> None:
    first = keyfile(tmp_path, "local.keys", 'A=from-local\nexport B="quoted # kept"\n')
    second = keyfile(tmp_path, "shared.keys", "A=from-shared\nC=only-shared # note\n")
    process = {"D": "from-shell", "A": ""}
    keys = KeyEnvironment.load(process, [first, tmp_path / "absent", second], tmp_path)
    assert keys["A"] == "from-local"
    assert keys["B"] == "quoted # kept"
    assert keys["C"] == "only-shared"
    assert keys["D"] == "from-shell"
    assert sorted(keys) == ["A", "B", "C", "D"]
    assert len(keys) == 4
    assert keys.where("A") == "local.keys:1"
    assert keys.where("C") == "shared.keys:2"
    assert keys.where("D") == "the environment"
    assert keys.where("E") == "nowhere"
    with pytest.raises(KeyError):
        keys["E"]


def test_an_empty_process_variable_is_unset(tmp_path: Path) -> None:
    keys = KeyEnvironment.load({"ONLY_SHELL": ""}, [], tmp_path)
    assert keys["ONLY_SHELL"] == ""
    assert keys.where("ONLY_SHELL") == "nowhere"


def test_a_file_outside_the_base_keeps_its_full_path(tmp_path: Path) -> None:
    inner = tmp_path / "project"
    inner.mkdir()
    keys = KeyEnvironment.load({}, [keyfile(tmp_path, "outer.keys", "K=v\n")], inner)
    assert keys.where("K") == f"{tmp_path / 'outer.keys'}:1"


def test_franca_reads_keys_from_the_same_environment(tmp_path: Path) -> None:
    """What the dry run reports is what a call would use: franca takes this mapping."""
    files = [keyfile(tmp_path, "project.keys", "ANTHROPIC_API_KEY=sk-file\n")]
    keys = KeyEnvironment.load({}, files, tmp_path)
    franca = SettingsKeyProvider(FrancaSettings(), keys)
    found = franca.key_for("anthropic")  # type: ignore[arg-type]
    assert found is not None
    assert found.get_secret_value() == "sk-file"
    assert key_source("anthropic", {}, keys) == "ANTHROPIC_API_KEY"
