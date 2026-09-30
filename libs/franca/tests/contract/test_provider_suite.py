"""The cross-provider contract suite: one set of assertions, every provider.

The shape is borrowed from LangChain's `ChatModelIntegrationTests` and Pydantic AI's
recorded-provider tests, adapted to franca's seams:

* **One suite, parametrised over providers.** Every case runs against every row in
  `PROVIDERS`, so a claim like "usage is normalised" is asserted for each dialect
  rather than argued for once. Adding a provider adds a column, not a file.
* **Skip, never fail, on a missing key.** A contributor without an OpenAI key still
  gets a meaningful Anthropic run. `-ra` prints the skip reason, so an absent
  provider is visible rather than silently green.
* **Assertions the wire guarantees, never the prose.** A live model's words are not
  reproducible; its token accounting, stop reason, served model and error taxonomy
  are. Nothing here asserts on generated text beyond it being non-empty.

**Cost.** The whole suite spends exactly **one completion per provider per run**: the
first test to need a response performs it and every later case reuses the cached
result. Every failure case is free, because a bad key and a bad model name are both
rejected before inference. So a full run is two billed requests of roughly twenty
tokens each, whatever the number of assertions.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from franca.chat.dialects.anthropic_messages import AnthropicMessagesAdapter
from franca.chat.dialects.openai_chat import OpenAIChatAdapter
from franca.chat.endpoints import ANTHROPIC_MESSAGES_ENDPOINT, OPENAI_CHAT_ENDPOINT
from franca.chat.ir import Item, ModelResponse, PromptPackage, SystemBlock
from franca.chat.model import ChatModel
from franca.chat.profile import ChatProfile
from franca.core.clock import AsyncioClock
from franca.core.connector import Connector
from franca.core.errors import ModelError
from franca.core.ids import ANTHROPIC, OPENAI
from franca.core.keys import StaticKeyProvider
from franca.transports.httpx import HttpxTransport

if TYPE_CHECKING:
    from collections.abc import Callable

    from franca.core.adapter import Adapter
    from franca.core.endpoint import Endpoint
    from franca.core.ids import Provider

pytestmark = pytest.mark.contract

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _dotenv(path: pathlib.Path) -> dict[str, str]:
    """Read `KEY=value` lines, ignoring blanks and comments. Returns {} if absent."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


_FILE_ENV = _dotenv(_REPO_ROOT / ".env")


def api_key(env_var: str) -> str | None:
    """Return the key for `env_var`, preferring the real environment over `.env`.

    Args:
        env_var: The variable to look up, e.g. `"ANTHROPIC_API_KEY"`.

    Returns:
        The key, or `None` when it is configured nowhere. An empty value counts as
        absent -- an exported-but-blank variable is a misconfiguration, not a key.
    """
    return os.environ.get(env_var) or _FILE_ENV.get(env_var) or None


@dataclass(frozen=True)
class ProviderCase:
    """One provider the live suite knows how to call.

    Attributes:
        name: The pytest id for this row.
        env_var: Where its key comes from.
        provider: The provider slug, for asserting the response carries it back.
        endpoint: The endpoint row to send to.
        adapter: Builds the dialect adapter; a factory so each test gets a fresh one.
        model: A cheap model on that provider. The suite sends a handful of tokens,
            so this is chosen for price rather than quality -- the suite tests the
            wire contract, and every model on a provider shares it.
    """

    name: str
    env_var: str
    provider: Provider
    endpoint: Endpoint
    adapter: Callable[[], Adapter[PromptPackage, ModelResponse]]
    model: str


PROVIDERS: tuple[ProviderCase, ...] = (
    ProviderCase(
        name="anthropic",
        env_var="ANTHROPIC_API_KEY",
        provider=ANTHROPIC,
        endpoint=ANTHROPIC_MESSAGES_ENDPOINT,
        adapter=AnthropicMessagesAdapter,
        model="claude-haiku-4-5-20251001",
    ),
    ProviderCase(
        name="openai",
        env_var="OPENAI_API_KEY",
        provider=OPENAI,
        endpoint=OPENAI_CHAT_ENDPOINT,
        adapter=OpenAIChatAdapter,
        model="gpt-4.1-nano",
    ),
)
"""Every provider the live suite exercises. Adding one is adding a row."""

IDS = [p.name for p in PROVIDERS]

PROBE = PromptPackage(
    system=(SystemBlock(text="You are terse. Answer in three words or fewer."),),
    items=(Item(role="user", kind="text", text="Name one primary colour."),),
    max_output_tokens=32,
)

_CACHE: dict[str, ModelResponse] = {}
_UNUSABLE: dict[str, str] = {}


def _model(case: ProviderCase, transport: HttpxTransport, *, key: str, model: str) -> ChatModel:
    """Assemble a live ChatModel for one provider."""
    return ChatModel(
        model=model,
        connector=Connector(
            case.endpoint,
            keys=StaticKeyProvider({case.provider: SecretStr(key)}),
            transport=transport,
            clock=AsyncioClock(),
        ),
        adapter=case.adapter(),
        profile=ChatProfile(provider=case.provider, model_prefix=""),
        clock=AsyncioClock(),
    )


def _require_key(case: ProviderCase) -> str:
    key = api_key(case.env_var)
    if not key:
        pytest.skip(f"{case.env_var} is not set; skipping the live {case.name} contract")
    return key


async def _response(case: ProviderCase) -> ModelResponse:
    """The one live completion for this provider, performed at most once per run.

    Caching here is what keeps the suite affordable: the assertions below are all
    facts about a single response, so paying for one and asserting many times is both
    cheaper and a stricter test than issuing a fresh request per assertion, which
    would let a flaky field pass on a lucky retry.
    """
    if case.name in _CACHE:
        return _CACHE[case.name]
    if case.name in _UNUSABLE:
        pytest.skip(_UNUSABLE[case.name])
    key = _require_key(case)
    transport = HttpxTransport()
    try:
        res = await _model(case, transport, key=key, model=case.model).complete(PROBE)
    except ModelError as exc:
        # A configured key on an account that cannot serve -- exhausted credits, a
        # disabled project, a revoked key -- is not a franca defect, so it skips
        # rather than fails, and it is recorded so the remaining cases skip without
        # re-issuing the request. A retryable failure is a genuine outage and is
        # left to fail loudly.
        if exc.failure_class == "auth" and not exc.retryable:
            _UNUSABLE[case.name] = f"{case.name} account cannot serve requests: {exc}"
            pytest.skip(_UNUSABLE[case.name])
        raise
    finally:
        await transport.aclose()
    _CACHE[case.name] = res
    return res


# ------------------------------------------------------------------ the one call


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_a_completion_returns_text(case: ProviderCase) -> None:
    res = await _response(case)
    assert isinstance(res.text, str)
    assert res.text.strip()


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_usage_is_normalised_across_dialects(case: ProviderCase) -> None:
    """`prompt_tokens` and `input_tokens` are the same meter once they reach the IR."""
    res = await _response(case)
    assert res.usage.input_tokens > 0, "the provider billed nothing for a non-empty prompt"
    assert res.usage.output_tokens > 0
    assert res.usage.cache_read_tokens >= 0
    assert res.usage.cache_write_tokens >= 0


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_response_identifies_its_own_route(case: ProviderCase) -> None:
    res = await _response(case)
    assert res.provider == case.provider
    assert res.dialect == case.endpoint.dialect


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_wire_reports_which_model_actually_served(case: ProviderCase) -> None:
    """How a silent alias or snapshot swap becomes visible instead of invisible."""
    res = await _response(case)
    assert res.served_model is not None
    assert res.served_model


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_provider_says_why_it_stopped(case: ProviderCase) -> None:
    res = await _response(case)
    assert res.stop_reason is not None


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_leaf_stamps_a_usable_trace(case: ProviderCase) -> None:
    res = await _response(case)
    trace = res.trace
    assert trace is not None, "trace is non-None on anything that leaves a leaf"
    assert trace.endpoint_id == case.endpoint.id
    assert trace.dialect == case.endpoint.dialect
    assert trace.selection_via == "native"
    assert trace.latency_ms > 0.0
    assert trace.attempts == 1


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_raw_body_survives_for_escape_hatches(case: ProviderCase) -> None:
    res = await _response(case)
    assert res.raw, "raw is the RawRepresentation escape hatch and must not be empty"
    assert "raw" not in res.model_dump(), "but it must never reach a dump"


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_no_key_material_reaches_the_response_or_its_dump(case: ProviderCase) -> None:
    key = _require_key(case)
    res = await _response(case)
    for rendered in (repr(res), str(res), res.model_dump_json()):
        assert key not in rendered


# ------------------------------------------------- free cases: rejected pre-inference


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_an_invalid_key_is_a_permanent_auth_failure(case: ProviderCase) -> None:
    """Free: rejected at the edge, so no tokens are billed."""
    _require_key(case)  # skip when the provider is not configured at all
    transport = HttpxTransport()
    try:
        model = _model(case, transport, key="definitely-not-a-valid-key", model=case.model)
        with pytest.raises(ModelError) as exc:
            await model.complete(PROBE)
    finally:
        await transport.aclose()

    assert exc.value.failure_class == "auth"
    assert exc.value.retryable is False, "no amount of retrying fixes a bad credential"
    assert exc.value.status in (401, 403)


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_an_invalid_key_is_never_echoed_back_into_the_error(case: ProviderCase) -> None:
    """OpenAI's 401 body quotes the submitted key; franca must not propagate it."""
    _require_key(case)
    sentinel = "sk-CANARY-must-not-appear-8812"
    transport = HttpxTransport()
    try:
        model = _model(case, transport, key=sentinel, model=case.model)
        with pytest.raises(ModelError) as exc:
            await model.complete(PROBE)
    finally:
        await transport.aclose()

    assert sentinel not in str(exc.value)
    assert sentinel not in repr(exc.value)


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_an_unknown_model_is_a_clean_non_retryable_error(case: ProviderCase) -> None:
    """Free: the request never reaches a model."""
    key = _require_key(case)
    transport = HttpxTransport()
    try:
        model = _model(case, transport, key=key, model="franca-no-such-model-9931")
        with pytest.raises(ModelError) as exc:
            await model.complete(PROBE)
    finally:
        await transport.aclose()

    assert exc.value.status in (400, 404)
    assert exc.value.retryable is False
    assert exc.value.provider == case.provider
    assert exc.value.raw is not None, "the provider's own payload stays available"


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_a_request_the_adapter_cannot_express_fails_before_the_wire(
    case: ProviderCase,
) -> None:
    """Free: no HTTP happens at all -- `build()` rejects it."""
    _require_key(case)
    transport = HttpxTransport()
    try:
        model = _model(case, transport, key="unused", model=case.model)
        unsendable = PromptPackage(items=(Item(role="user", kind="image", text="x"),))
        with pytest.raises(ModelError) as exc:
            await model.complete(unsendable)
    finally:
        await transport.aclose()

    assert exc.value.failure_class == "unsupported"
    assert exc.value.status is None, "nothing was ever sent, so there is no HTTP status"


# ------------------------------------------------------------------ divergence


@pytest.mark.parametrize("case", PROVIDERS, ids=IDS)
async def test_the_environment_variable_is_the_documented_one(case: ProviderCase) -> None:
    """Free: pins the env-var names this project promises, without reading a value."""
    assert case.env_var in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"}
    assert api_key(case.env_var) is not None or case.env_var not in os.environ
