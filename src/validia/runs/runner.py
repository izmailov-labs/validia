"""Run a suite against a model: every case, every repetition, graded and summed up.

One *trial* is one case sent once. The suite's prompt is the system text, the case's
input the user turn, and the suite's tools are offered; the reply is graded by the case,
exactly as ``validia check`` grades a reply typed in by hand. Repetitions make a pass
rate an estimate rather than a single draw, so the summary reports it with a 95%
interval, and a call that failed -- a timeout, a rate limit that outlived its retries --
is counted as an error rather than as a wrong answer, because it is not one.

This module is loop-neutral, like franca beneath it: time goes through an injected
:class:`~franca.core.clock.Clock`, HTTP through an injected transport, and nothing here
imports asyncio. :func:`run_trial` is the unit of work; scheduling trials -- how many in
flight, and the event loop they run on -- belongs to the caller. The ``validia run``
command does it with asyncio; a trio user does it with a nursery.
"""

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from importlib.metadata import version
from typing import Any, Final

from franca.chat.dialects.anthropic_messages import AnthropicMessagesAdapter
from franca.chat.dialects.google_generate_content import GoogleGenerateContentAdapter
from franca.chat.dialects.openai_chat import OpenAIChatAdapter
from franca.chat.endpoints import (
    ANTHROPIC_MESSAGES_ENDPOINT,
    DEEPSEEK_CHAT_ENDPOINT,
    GOOGLE_GENERATE_CONTENT_ENDPOINT,
    OPENAI_CHAT_ENDPOINT,
    XAI_CHAT_ENDPOINT,
)
from franca.chat.ir import Item, ModelResponse, PromptPackage, SystemBlock, ToolDef
from franca.chat.model import ChatClient, ChatModel
from franca.chat.profiles import CHAT_PROFILES
from franca.core.adapter import Adapter
from franca.core.clock import Clock
from franca.core.connector import Connector
from franca.core.endpoint import Endpoint
from franca.core.errors import ModelError
from franca.core.ids import ANTHROPIC, DEEPSEEK, GOOGLE, OPENAI, XAI, Provider
from franca.core.keys import KeyProvider
from franca.core.settings import ProviderSettings
from franca.core.transport import Transport

from ..suites.expect import Reply, ToolCall
from ..suites.suite import Case, Suite
from .access import AccessError

__all__ = [
    "FATAL",
    "SUPPORTED",
    "CaseResult",
    "RunSummary",
    "Trial",
    "TrialResult",
    "build_model",
    "package",
    "reply_of",
    "run_trial",
    "summarize",
    "trials",
    "unsupported",
    "wilson",
]

_ROUTES: Final[Mapping[str, tuple[Endpoint, type[Adapter[PromptPackage, ModelResponse]]]]] = {
    ANTHROPIC: (ANTHROPIC_MESSAGES_ENDPOINT, AnthropicMessagesAdapter),
    OPENAI: (OPENAI_CHAT_ENDPOINT, OpenAIChatAdapter),
    GOOGLE: (GOOGLE_GENERATE_CONTENT_ENDPOINT, GoogleGenerateContentAdapter),
    XAI: (XAI_CHAT_ENDPOINT, OpenAIChatAdapter),
    DEEPSEEK: (DEEPSEEK_CHAT_ENDPOINT, OpenAIChatAdapter),
}

SUPPORTED: Final = tuple(_ROUTES)
"""The providers ``validia run`` can call: each has a franca endpoint row and adapter."""

FATAL: Final = frozenset({"auth", "unsupported", "selection"})
"""Failure classes that fail every trial alike -- a bad key, a retired model, no route to
it -- so a run stops at the first one rather than sending every remaining trial to the
same answer."""

_BACKOFF_S: Final = 1.0
_BACKOFF_CAP_S: Final = 30.0


# ------------------------------------------------------------------ the model


def build_model(
    provider: str,
    model: str,
    *,
    keys: KeyProvider,
    transport: Transport,
    clock: Clock,
    settings: ProviderSettings | None = None,
) -> ChatModel:
    """Assemble franca's chat model for one provider and model id.

    Args:
        provider: The provider's slug, as in ``anthropic``.
        model: The model id to send, as in ``claude-sonnet-5-5``.
        keys: Where the API key is read from, at call time.
        transport: The HTTP implementation; a scripted one in tests.
        clock: Source of time for latency and retry delays.
        settings: ``[providers.<name>]``: a ``base_url`` (a gateway, a proxy), a
            ``timeout_s`` and ``extra_headers``.

    Returns:
        The model, ready for ``await model.complete(package)``.

    Raises:
        AccessError: If franca has no chat endpoint for the provider.
    """
    route = _ROUTES.get(provider)
    if route is None:
        msg = f"cannot run {provider}:{model}: validia can call {', '.join(SUPPORTED)}"
        raise AccessError(msg)
    endpoint, adapter = route
    chosen = settings or ProviderSettings()
    if chosen.base_url:
        endpoint = endpoint.model_copy(update={"base_url": chosen.base_url})
    connector = Connector(
        endpoint,
        keys=keys,
        transport=transport,
        clock=clock,
        timeout_s=chosen.timeout_s,
        extra_headers=chosen.extra_headers,
    )
    return ChatModel(
        model=model,
        connector=connector,
        adapter=adapter(),
        profile=CHAT_PROFILES.resolve(Provider(provider), model),
        clock=clock,
    )


def package(suite: Suite, prompt: str, case: Case) -> PromptPackage:
    """Turn one case into the request sent for it.

    Args:
        suite: The suite, for its tools.
        prompt: The prompt under test, read from the suite's prompt file.
        case: The case.

    Returns:
        The package: the prompt as system text, the case's input as the user turn,
        and the suite's tools, if it has any.
    """
    return PromptPackage(
        system=(SystemBlock(text=prompt),),
        items=(Item(role="user", kind="text", text=case.input),),
        tools=tuple(
            ToolDef(
                name=tool.name,
                parameters=tool.parameters,
                description=tool.description,
                strict=tool.strict,
            )
            for tool in suite.tools
        ),
    )


def reply_of(response: ModelResponse) -> Reply:
    """Read the model's answer the way a grader sees it: its text and its tool calls.

    Args:
        response: franca's response.

    Returns:
        The reply: every text item joined, then every tool call in order.
    """
    spoken = [item for item in response.items if item.role in ("assistant", "model")]
    text = "".join(item.text or "" for item in spoken if item.kind == "text")
    calls = tuple(
        ToolCall(item.name or "", dict(item.args or {}))
        for item in spoken
        if item.kind == "tool_call"
    )
    return Reply(text=text, tool_calls=calls)


def unsupported(suite: Suite) -> str | None:
    """Say why a suite cannot be run through franca yet, if it cannot.

    franca's adapters send text turns only, for now: a package's tools never reach the
    wire, and a tool call in the reply is not read back. A tool suite run anyway would
    grade every case as "called no tool" -- a wrong answer the model never gave -- so it
    is refused instead, until franca carries tools and validia's floor on it moves up.

    Args:
        suite: The suite.

    Returns:
        The reason, or ``None`` when the suite can run.
    """
    if suite.tools or suite.grade.type == "tool":
        return (
            f"tool suites need tool calling, which franca {version('franca')} does not do yet:"
            " its adapters send text turns only, so the tools would never reach the model."
            " --dry-run still checks the suite"
        )
    return None


# ------------------------------------------------------------------ trials


@dataclass(frozen=True, slots=True)
class Trial:
    """One case, sent once.

    Attributes:
        case: The case.
        rep: Which repetition this is, from 1.
    """

    case: Case
    rep: int


def trials(suite: Suite, reps: int) -> list[Trial]:
    """Every trial a run makes: each case, ``reps`` times, case by case.

    Args:
        suite: The suite.
        reps: Repetitions of every case; at least 1.

    Returns:
        The trials, in the order they are reported.
    """
    return [Trial(case, rep) for case in suite.cases for rep in range(1, reps + 1)]


@dataclass(frozen=True, slots=True)
class TrialResult:
    """What one trial came to.

    Attributes:
        case: The case's id.
        rep: Which repetition, from 1.
        tags: The case's tags; the first is its group.
        passed: Whether the reply was right; ``None`` when no reply came back.
        reason: Why it failed: the grader's reason, or the call's error. Empty on a pass.
        reply: The reply's text, for the record.
        tool_calls: The tools it called, as ``name(args)``.
        error: The failure class of a call that failed, as franca names it.
        input_tokens: Tokens the call read.
        output_tokens: Tokens it wrote.
        cache_read_tokens: Input tokens served from the provider's cache.
        latency_ms: How long the last attempt took on the wire.
        attempts: How many calls the trial took, retries included.
        served_model: The model the provider says answered.
        stop_reason: Why it stopped, as the provider says.
    """

    case: str
    rep: int
    tags: tuple[str, ...]
    passed: bool | None
    reason: str = ""
    reply: str = ""
    tool_calls: tuple[str, ...] = ()
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: float = 0.0
    attempts: int = 1
    served_model: str | None = None
    stop_reason: str | None = None

    def to_json(self) -> str:
        """Render the result as one line of JSON, for ``trials.jsonl``."""
        return json.dumps(asdict(self), ensure_ascii=False)


async def run_trial(
    client: ChatClient,
    suite: Suite,
    prompt: str,
    trial: Trial,
    *,
    clock: Clock,
    retries: int = 2,
) -> TrialResult:
    """Send one trial, retrying what is worth retrying, and grade the reply.

    A failure franca marks retryable -- a rate limit, an overloaded server, a dropped
    connection -- is retried after the delay the provider asked for, or after an
    exponential backoff when it asked for none. Anything else, and a retryable failure
    that outlives its retries, ends the trial as an error.

    Args:
        client: The model, or a pipeline of middleware around it.
        suite: The suite, for its tools.
        prompt: The prompt under test.
        trial: The trial.
        clock: Where retry delays are slept; injected, so tests never wait.
        retries: Extra attempts a retryable failure gets.

    Returns:
        The trial's result. A failed call is a result, not an exception.
    """
    request = package(suite, prompt, trial.case)
    attempts = 0
    response: ModelResponse | None = None
    while response is None:
        attempts += 1
        try:
            response = await client.complete(request)
        except ModelError as exc:
            if not exc.retryable or attempts > retries:
                return TrialResult(
                    case=trial.case.id,
                    rep=trial.rep,
                    tags=trial.case.tags,
                    passed=None,
                    reason=exc.message,
                    error=str(exc.failure_class),
                    attempts=attempts,
                )
            backoff = min(_BACKOFF_CAP_S, _BACKOFF_S * 2 ** (attempts - 1))
            await clock.sleep(exc.retry_after_s if exc.retry_after_s is not None else backoff)
    reply = reply_of(response)
    verdict = trial.case.check(reply)
    usage = response.usage
    trace = response.trace
    return TrialResult(
        case=trial.case.id,
        rep=trial.rep,
        tags=trial.case.tags,
        passed=verdict.passed,
        reason=verdict.reason,
        reply=reply.text,
        tool_calls=tuple(
            f"{call.name}({json.dumps(call.args, sort_keys=True)})" for call in reply.tool_calls
        ),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        latency_ms=round(trace.latency_ms, 1) if trace is not None else 0.0,
        attempts=attempts,
        served_model=response.served_model,
        stop_reason=response.stop_reason,
    )


# ------------------------------------------------------------------ the summary


def wilson(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for a pass rate: honest at 0%, 100% and small n.

    Args:
        passed: Trials that passed.
        total: Trials graded.
        z: The normal quantile; 1.96 gives a 95% interval.

    Returns:
        ``(low, high)`` as fractions; ``(0.0, 1.0)`` when nothing was graded.
    """
    if total == 0:
        return 0.0, 1.0
    rate = passed / total
    centre = rate + z * z / (2 * total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    scale = 1 + z * z / total
    return max(0.0, (centre - spread) / scale), min(1.0, (centre + spread) / scale)


def _percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One case across its repetitions.

    Attributes:
        case: The case's id.
        group: Its first tag, or ``untagged``.
        passed: Repetitions that passed.
        graded: Repetitions that got a reply to grade.
        errors: Repetitions whose call failed.
        reason: The first failure's reason, for the report.
    """

    case: str
    group: str
    passed: int
    graded: int
    errors: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """A run, summed up.

    Attributes:
        trials: Trials run.
        passed: Trials that passed.
        graded: Trials that got a reply to grade.
        errors: Failure classes of the trials whose call failed, and how many of each.
        interval: The 95% Wilson interval of the pass rate, as fractions.
        cases: Every case, in suite order.
        groups: Each group's ``(passed, graded)``, in suite order.
        input_tokens: Tokens read, across the run.
        output_tokens: Tokens written, across the run.
        cache_read_tokens: Input tokens served from cache, across the run.
        latency_p50_ms: The median latency of the calls that answered.
        latency_p95_ms: Their 95th percentile.
        served_models: Every model the provider says answered.
    """

    trials: int
    passed: int
    graded: int
    errors: Mapping[str, int]
    interval: tuple[float, float]
    cases: tuple[CaseResult, ...]
    groups: Mapping[str, tuple[int, int]]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    latency_p50_ms: float
    latency_p95_ms: float
    served_models: tuple[str, ...] = field(default=())

    @property
    def rate(self) -> float | None:
        """The pass rate over graded trials; ``None`` when nothing was graded."""
        return self.passed / self.graded if self.graded else None

    def to_dict(self) -> dict[str, Any]:
        """Render the summary as plain data, for ``summary.json``."""
        data = asdict(self)
        data["rate"] = self.rate
        data["groups"] = {name: {"passed": p, "graded": g} for name, (p, g) in self.groups.items()}
        return data


def summarize(results: Sequence[TrialResult]) -> RunSummary:
    """Sum up a run's trial results.

    Args:
        results: Every trial's result, in trial order.

    Returns:
        The summary: overall, by case, by group, and the cost.
    """
    graded = [result for result in results if result.passed is not None]
    passed = sum(1 for result in graded if result.passed)
    by_case: dict[str, list[TrialResult]] = {}
    for result in results:
        by_case.setdefault(result.case, []).append(result)
    cases = []
    groups: dict[str, list[int]] = {}
    for case, runs in by_case.items():
        group = runs[0].tags[0] if runs[0].tags else "untagged"
        ok = sum(1 for run in runs if run.passed)
        seen = sum(1 for run in runs if run.passed is not None)
        failure = next((run.reason for run in runs if not run.passed), "")
        cases.append(
            CaseResult(case, group, ok, seen, sum(1 for run in runs if run.passed is None), failure)
        )
        tally = groups.setdefault(group, [0, 0])
        tally[0] += ok
        tally[1] += seen
    latencies = [result.latency_ms for result in graded]
    return RunSummary(
        trials=len(results),
        passed=passed,
        graded=len(graded),
        errors=dict(Counter(result.error for result in results if result.error is not None)),
        interval=wilson(passed, len(graded)),
        cases=tuple(cases),
        groups={name: (tally[0], tally[1]) for name, tally in groups.items()},
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        cache_read_tokens=sum(result.cache_read_tokens for result in results),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        served_models=tuple(
            dict.fromkeys(result.served_model for result in results if result.served_model)
        ),
    )
