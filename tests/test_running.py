"""The runner: requests built from cases, replies graded, failures retried, runs summed up."""

from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from franca.chat.model import ChatModel
from franca.core.keys import StaticKeyProvider
from franca.testing import FakeClock
from pydantic import SecretStr

from validia.runs.access import AccessError
from validia.runs.runner import (
    Trial,
    TrialResult,
    build_model,
    package,
    reply_of,
    run_trial,
    summarize,
    trials,
    unsupported,
    wilson,
)
from validia.suites import scaffold
from validia.suites.expect import Label, ToolCall, ToolUse
from validia.suites.suite import AnswerType, Case, Suite, load_suite

KEYS = StaticKeyProvider(
    {name: SecretStr("sk-test") for name in ("anthropic", "openai", "google")}  # type: ignore[misc]
)


def example(tmp_path: Path, answer: AnswerType = "label") -> Suite:
    """One of `validia init`'s example suites, written into a project."""
    folder = tmp_path / "evals" / scaffold.EXAMPLES[answer]
    folder.mkdir(parents=True)
    where = PurePosixPath("evals", scaffold.EXAMPLES[answer], "suite.toml")
    for name in scaffold.suite_files(answer):
        (folder / name).write_text(scaffold.render_suite(answer, name, where), encoding="utf-8")
    return load_suite(folder / "suite.toml")


def model(wire: Any, clock: FakeClock, provider: str = "anthropic", name: str = "m-1") -> ChatModel:
    return build_model(provider, name, keys=KEYS, transport=wire, clock=clock)


def test_a_case_becomes_a_system_prompt_a_user_turn_and_the_tools(tmp_path: Path) -> None:
    suite = example(tmp_path, "tool")
    request = package(suite, "Be helpful.", suite.cases[0])
    assert [block.text for block in request.system] == ["Be helpful."]
    assert [(item.role, item.kind, item.text) for item in request.items] == [
        ("user", "text", suite.cases[0].input)
    ]
    assert [tool.name for tool in request.tools] == [tool.name for tool in suite.tools]
    assert request.tools[0].parameters == suite.tools[0].parameters


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_each_provider_s_reply_is_read_and_graded(
    tmp_path: Path, provider: str, fake_wire: Any
) -> None:
    suite = example(tmp_path)
    wire = fake_wire(lambda text: "urgent" if "noon" in text else "normal")
    clock = FakeClock(step=0.25)
    case = suite.cases[0]
    result = await run_trial(
        model(wire, clock, provider), suite, "Triage.", Trial(case, 1), clock=clock
    )
    assert result.passed is True
    assert (result.reply, result.input_tokens, result.output_tokens) == ("urgent", 100, 5)
    assert result.served_model == "served-model-1"
    assert result.latency_ms == 250.0
    assert result.attempts == 1


def test_a_tool_suite_waits_for_franca_to_carry_tools(tmp_path: Path) -> None:
    reason = unsupported(example(tmp_path, "tool"))
    assert reason is not None
    assert reason.startswith("tool suites need tool calling, which franca")
    assert unsupported(example(tmp_path / "other")) is None


def test_a_tool_call_in_a_reply_is_read_with_its_arguments() -> None:
    from franca.chat.ir import Item, ModelResponse
    from franca.core.ids import ANTHROPIC, ANTHROPIC_MESSAGES

    response = ModelResponse(
        model="m-1",
        provider=ANTHROPIC,
        dialect=ANTHROPIC_MESSAGES,
        items=(
            Item(role="assistant", kind="text", text="Let me look. "),
            Item(role="assistant", kind="tool_call", name="lookup_order", args={"id": "48213"}),
            Item(role="assistant", kind="text", text="Done."),
            Item(role="user", kind="text", text="not the model's"),
        ),
    )
    reply = reply_of(response)
    assert reply.text == "Let me look. Done."
    assert reply.tool_calls == (ToolCall("lookup_order", {"id": "48213"}),)
    assert ToolUse("lookup_order", {"id": "48213"}).check(reply).passed


async def test_a_retryable_failure_waits_as_asked_then_tries_again(
    tmp_path: Path, fake_wire: Any
) -> None:
    suite = example(tmp_path)
    wire = fake_wire(lambda _: "urgent", failures=[fake_wire.RATE_LIMITED, fake_wire.RATE_LIMITED])
    clock = FakeClock()
    result = await run_trial(model(wire, clock), suite, "T.", Trial(suite.cases[0], 1), clock=clock)
    assert (result.passed, result.attempts) == (True, 3)
    assert clock.slept == [3.0, 3.0]  # the provider's retry-after, each time


async def test_retries_run_out_and_the_trial_is_an_error_not_a_wrong_answer(
    tmp_path: Path, fake_wire: Any
) -> None:
    suite = example(tmp_path)
    wire = fake_wire(lambda _: "urgent", failures=[fake_wire.RATE_LIMITED] * 3)
    clock = FakeClock()
    result = await run_trial(
        model(wire, clock), suite, "T.", Trial(suite.cases[0], 1), clock=clock, retries=1
    )
    assert result.passed is None
    assert (result.error, result.attempts) == ("rate_limit", 2)
    assert len(wire.requests) == 2


async def test_a_failure_not_worth_retrying_ends_the_trial_at_once(
    tmp_path: Path, fake_wire: Any
) -> None:
    suite = example(tmp_path)
    wire = fake_wire(lambda _: "urgent", failures=[fake_wire.BAD_KEY])
    clock = FakeClock()
    result = await run_trial(model(wire, clock), suite, "T.", Trial(suite.cases[0], 1), clock=clock)
    assert (result.passed, result.error, result.attempts) == (None, "auth", 1)
    assert clock.slept == []


def test_only_the_providers_franca_can_reach(fake_wire: Any) -> None:
    with pytest.raises(AccessError, match="cannot run meta:llama-4: validia can call anthropic"):
        build_model("meta", "llama-4", keys=KEYS, transport=fake_wire(str), clock=FakeClock())


async def test_a_base_url_sends_to_a_gateway(tmp_path: Path, fake_wire: Any) -> None:
    from franca.core.settings import ProviderSettings

    built = build_model(
        "openai",
        "gpt-5",
        keys=KEYS,
        transport=fake_wire(str),
        clock=FakeClock(),
        settings=ProviderSettings(base_url="https://gateway.example/v1", timeout_s=5),
    )
    assert built.connector.endpoint.base_url == "https://gateway.example/v1"


def test_trials_repeat_each_case_in_turn(tmp_path: Path) -> None:
    suite = example(tmp_path)
    plan = trials(suite, 2)
    assert len(plan) == 2 * len(suite.cases)
    assert [(t.case.id, t.rep) for t in plan[:3]] == [
        (suite.cases[0].id, 1),
        (suite.cases[0].id, 2),
        (suite.cases[1].id, 1),
    ]


def result(case: str, passed: bool | None, group: str = "a", **fields: object) -> TrialResult:
    return TrialResult(case=case, rep=1, tags=(group,), passed=passed, **fields)  # type: ignore[arg-type]


def test_a_summary_counts_by_case_and_group_and_keeps_errors_apart() -> None:
    summary = summarize(
        [
            result("x", True, latency_ms=100.0, input_tokens=10, output_tokens=1),
            result("x", False, reason="wrong", latency_ms=300.0, input_tokens=10, output_tokens=1),
            result("y", True, "b", latency_ms=200.0, served_model="m-1"),
            result("y", None, "b", reason="slow down", error="rate_limit"),
        ]
    )
    assert (summary.trials, summary.graded, summary.passed) == (4, 3, 2)
    assert summary.rate == pytest.approx(2 / 3)
    assert summary.errors == {"rate_limit": 1}
    assert summary.groups == {"a": (1, 2), "b": (1, 1)}
    assert [(c.case, c.passed, c.graded, c.errors, c.reason) for c in summary.cases] == [
        ("x", 1, 2, 0, "wrong"),
        ("y", 1, 1, 1, "slow down"),
    ]
    assert (summary.latency_p50_ms, summary.latency_p95_ms) == (200.0, 300.0)
    assert (summary.input_tokens, summary.output_tokens) == (20, 2)
    assert summary.served_models == ("m-1",)
    assert summary.to_dict()["groups"] == {
        "a": {"passed": 1, "graded": 2},
        "b": {"passed": 1, "graded": 1},
    }
    nothing = summarize([result("z", None, error="auth")])
    assert (nothing.rate, nothing.interval, nothing.latency_p50_ms) == (None, (0.0, 1.0), 0.0)


@pytest.mark.parametrize(
    ("passed", "total", "low", "high"),
    [
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (5, 10, 0.2366, 0.7634),
        (18, 24, 0.5510, 0.8800),
    ],
)
def test_the_wilson_interval(passed: int, total: int, low: float, high: float) -> None:
    got = wilson(passed, total)
    assert got == pytest.approx((low, high), abs=1e-4)


def test_an_empty_reply_never_passes(tmp_path: Path) -> None:
    case = Case(id="c", input="hi", expected=Label("urgent"))
    from validia.suites.expect import Reply

    assert case.check(Reply()).reason == "empty reply"
