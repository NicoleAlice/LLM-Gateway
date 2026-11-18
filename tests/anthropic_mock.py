"""A real ``AsyncAnthropic`` client whose HTTP layer is a mock: no network, no cost."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2

API_KEY = "sk-ant-test-0123456789abcdef"  # fake; asserted never to leak
UPSTREAM_SECRET = "UPSTREAM-SAYS-the-user-wrote-PASSWORD-123"  # stands in for an echoed prompt


def message(
    *,
    content: list[dict[str, Any]] | None = None,
    stop_reason: str | None = "end_turn",
    model: str = "claude-opus-5-5",
    input_tokens: int = 12,
    output_tokens: int = 7,
) -> dict[str, Any]:
    """A Messages API response body. By default: a thinking block, then two text blocks."""
    if content is None:
        content = [
            {"type": "thinking", "thinking": "private reasoning", "signature": "sig"},
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": " world"},
        ]
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def error_body(status: int) -> dict[str, Any]:
    kind = {400: "invalid_request_error", 429: "rate_limit_error", 529: "overloaded_error"}
    return {
        "type": "error",
        "error": {"type": kind.get(status, "api_error"), "message": UPSTREAM_SECRET},
    }


class Recorder:
    """A mock transport handler: records every request, answers with ``respond``."""

    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.respond = respond
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)  # type: ignore[no-any-return]


def answering(status: int, body: dict[str, Any]) -> Recorder:
    return Recorder(lambda _request: httpx2.Response(status, json=body))


def raising(exc: Exception) -> Recorder:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        raise exc

    return Recorder(respond)


def client_for(handler: Recorder) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=API_KEY,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
