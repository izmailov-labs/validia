"""Built-in chat profile rows: what franca has *measured* about specific models.

A profile row is the answer to "one vendor, one contract?" -- which is no. Every fact
below was obtained by sending the parameter to the live API and recording whether the
request was legal, not by reading documentation, which is why `verified` carries a
date and `source` is left unset: the source is the wire.

The measurement that motivates the whole table, taken on 2026-09-07 by sending
`max_tokens=1` with each parameter in turn:

    parameter                haiku-4-5  sonnet-4-5  opus-4-5  sonnet-5  opus-5
    temperature / top_p      accepted   accepted    accepted  400       400
    thinking.enabled+budget  accepted   accepted    accepted  400       400
    thinking.adaptive        400        400         400       accepted  accepted
    output_config.effort     400        400         accepted  accepted  accepted
    assistant prefill        accepted   accepted    accepted  accepted  accepted

Three groups, not two, and the boundaries do not line up: `effort` splits
`opus-4-5` away from its own generation, while sampling and thinking split it the
other way. No single "anthropic" branch can be right for all five, which is the
argument for per-model rows resolved by longest prefix rather than `if provider ==`.

Tri-state matters here. `False` means *measured absent* -- a request carrying that
parameter is a 400, so franca can refuse it locally with a field path instead of
paying a round trip. `None` means nobody has checked, which is not the same claim and
must not be silently treated as `False`.

Rows are keyed by `model_prefix` and matched after `normalize_model_id`, so a dated
snapshot (`claude-haiku-4-5-20251001`), an aliased id and a `[1m]` context suffix all
land on the same row.
"""

from datetime import date
from typing import Final

from franca.chat.profile import ChatProfile
from franca.core.enums import ThinkingMode
from franca.core.ids import ANTHROPIC, OPENAI
from franca.core.profile import ProfileTable

_MEASURED: Final = date(2026, 9, 7)

_ANTHROPIC_LEGACY_SAMPLING: Final = ChatProfile(
    provider=ANTHROPIC,
    model_prefix="claude-haiku-4-5",
    verified=_MEASURED,
    sampling_allowed=True,
    thinking=ThinkingMode.budget,
    budget_tokens_allowed=True,
    prefill_allowed=True,
    effort_levels=(),
    streaming=True,
    notes="effort rejected: 'This model does not support the effort parameter.'",
)

_ANTHROPIC_SONNET_45: Final = _ANTHROPIC_LEGACY_SAMPLING.model_copy(
    update={"model_prefix": "claude-sonnet-4-5"}
)

_ANTHROPIC_OPUS_45: Final = ChatProfile(
    provider=ANTHROPIC,
    model_prefix="claude-opus-4-5",
    verified=_MEASURED,
    sampling_allowed=True,
    thinking=ThinkingMode.budget,
    budget_tokens_allowed=True,
    prefill_allowed=True,
    effort_levels=("high",),
    streaming=True,
    notes="the odd one out: takes effort AND legacy sampling. Only 'high' was measured.",
)

_ANTHROPIC_ADAPTIVE: Final = ChatProfile(
    provider=ANTHROPIC,
    model_prefix="claude-sonnet-5",
    verified=_MEASURED,
    sampling_allowed=False,
    thinking=ThinkingMode.adaptive,
    budget_tokens_allowed=False,
    prefill_allowed=True,
    effort_levels=("high",),
    streaming=True,
    notes=(
        "temperature and top_p are 400 ('deprecated for this model');"
        " thinking.type=enabled is 400, adaptive is required."
    ),
)

_ANTHROPIC_OPUS_5: Final = _ANTHROPIC_ADAPTIVE.model_copy(update={"model_prefix": "claude-opus-5"})

_ANTHROPIC_DEFAULT: Final = ChatProfile(
    provider=ANTHROPIC,
    model_prefix="",
    streaming=True,
    notes="fallback for an unmeasured Anthropic model: every capability stays None.",
)
"""Everything unknown is `None`, never `False`.

A default row exists so an unrecognised model resolves at all, but it must not
*claim* anything. Guessing `False` here would make franca refuse a request the model
would have accepted; guessing `True` would send a parameter that 400s. `None` is the
honest third answer, and the unverified-feature policy decides what to do with it.
"""

_OPENAI_DEFAULT: Final = ChatProfile(
    provider=OPENAI,
    model_prefix="",
    streaming=True,
    notes=(
        "unmeasured: the account carried no credits when the Anthropic rows were taken,"
        " and OpenAI checks quota before it validates parameters, so every probe"
        " returned 429 rather than a usable 400/200 verdict."
    ),
)
"""OpenAI rows are deliberately empty of capability claims.

This is a measurement gap, not a finding. The known divergences on that side --
`max_tokens` versus `max_completion_tokens`, reasoning models rejecting sampling
parameters -- are real but were not reproducible here, so recording them as verified
would be inventing evidence. They stay `None` until a funded account can answer.
"""

CHAT_PROFILES: Final = ProfileTable[ChatProfile](
    verified_on=_MEASURED,
    profiles=(
        _ANTHROPIC_LEGACY_SAMPLING,
        _ANTHROPIC_SONNET_45,
        _ANTHROPIC_OPUS_45,
        _ANTHROPIC_ADAPTIVE,
        _ANTHROPIC_OPUS_5,
    ),
    defaults={ANTHROPIC: _ANTHROPIC_DEFAULT, OPENAI: _OPENAI_DEFAULT},
)
"""Every measured chat model, resolved by longest matching `model_prefix`.

`resolve(provider, model)` overlays the matched row onto the provider default with
`exclude_unset`, so a row states only what it measured and inherits the rest.
"""
