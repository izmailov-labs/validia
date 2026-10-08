"""Shared test doubles: a transport that answers like a provider's API, without a network."""

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest
from franca.core.transport import RawResponse

Answer = str | tuple[str, dict[str, Any]]
"""What the fake model says: text, or a tool call as ``(name, args)``."""


class FakeWire:
    """A `Transport` that answers each request in the wire format its URL is for.

    `answer` reads the user's message and decides the reply; `failures` are answered
    first, in order, as ``(status, headers, body)``. Every request body is kept in
    `requests`, decoded, so a test can assert on what was sent.
    """

    RATE_LIMITED: tuple[int, dict[str, str], str] = (
        429,
        {"retry-after": "3"},
        '{"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}',
    )
    BAD_KEY: tuple[int, dict[str, str], str] = (
        401,
        {},
        '{"type": "error", "error": {"type": "authentication_error", "message": "invalid key"}}',
    )

    def __init__(
        self,
        answer: Callable[[str], Answer],
        failures: Sequence[tuple[int, Mapping[str, str], str]] = (),
        served: str = "served-model-1",
    ) -> None:
        self.answer = answer
        self.failures = list(failures)
        self.served = served
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self, url: str, content: bytes, headers: Mapping[str, str], *, timeout_s: float
    ) -> RawResponse:
        """Answer the next failure, or a reply in the wire format the URL is for."""
        body = json.loads(content)
        self.requests.append(body)
        if self.failures:
            status, extra, text = self.failures.pop(0)
            return RawResponse(status=status, headers=dict(extra), body=text.encode())
        if "generativelanguage" in url:
            return _ok(self._google(body))
        if "anthropic" in url:
            return _ok(self._anthropic(body))
        return _ok(self._openai(body))

    async def get(self, url: str, headers: Mapping[str, str], *, timeout_s: float) -> RawResponse:
        """Refuse: a chat call never GETs."""
        raise AssertionError("no GET expected")

    def post_stream(self, *args: object, **kwargs: object) -> Any:
        """Refuse: nothing here streams."""
        raise AssertionError("no stream expected")

    async def aclose(self) -> None:
        """Record that the run released the transport."""
        self.closed = True

    def _anthropic(self, body: dict[str, Any]) -> dict[str, Any]:
        said = body["messages"][-1]["content"]
        text = said if isinstance(said, str) else " ".join(b.get("text", "") for b in said)
        reply = self.answer(text)
        if isinstance(reply, tuple):
            block: dict[str, Any] = {
                "type": "tool_use",
                "id": "toolu_1",
                "name": reply[0],
                "input": reply[1],
            }
            stop = "tool_use"
        else:
            block, stop = {"type": "text", "text": reply}, "end_turn"
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": self.served,
            "content": [block],
            "stop_reason": stop,
            "usage": {"input_tokens": 100, "output_tokens": 5},
        }

    def _openai(self, body: dict[str, Any]) -> dict[str, Any]:
        said = next(m for m in reversed(body["messages"]) if m["role"] == "user")["content"]
        text = said if isinstance(said, str) else " ".join(b.get("text", "") for b in said)
        reply = self.answer(text)
        message: dict[str, Any] = {"role": "assistant", "content": None}
        if isinstance(reply, tuple):
            message["tool_calls"] = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": reply[0], "arguments": json.dumps(reply[1])},
                }
            ]
            finish = "tool_calls"
        else:
            message["content"], finish = reply, "stop"
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": self.served,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }

    def _google(self, body: dict[str, Any]) -> dict[str, Any]:
        text = " ".join(part.get("text", "") for part in body["contents"][-1]["parts"])
        reply = self.answer(text)
        part = (
            {"functionCall": {"name": reply[0], "args": reply[1]}}
            if isinstance(reply, tuple)
            else {"text": reply}
        )
        return {
            "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5},
            "modelVersion": self.served,
        }


def _ok(data: dict[str, Any]) -> RawResponse:
    return RawResponse(status=200, headers={}, body=json.dumps(data).encode())


@pytest.fixture
def fake_wire() -> type[FakeWire]:
    """The fake transport's class: build one per test with the answers it needs."""
    return FakeWire
