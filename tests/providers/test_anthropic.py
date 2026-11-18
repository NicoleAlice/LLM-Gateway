import json
import logging
from typing import Any

import httpx2
import pytest

from llm_gateway.providers.anthropic import AnthropicProvider
from llm_gateway.providers.base import (
    ChatMessage,
    ChatRequest,
    ProviderError,
    ProviderRejectedRequest,
    Usage,
)
from tests.anthropic_mock import (
    API_KEY,
    UPSTREAM_SECRET,
    Recorder,
    answering,
    client_for,
    error_body,
    message,
    raising,
)

MODEL = "claude-opus-5-5"


def _req(*pairs: tuple[str, str], **kw: Any) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role=r, content=c) for r, c in pairs], **kw)  # type: ignore[arg-type]


async def _call(handler: Recorder, request: ChatRequest | None = None, model: str = MODEL) -> Any:
    provider = AnthropicProvider(client_for(handler))
    return await provider.chat(request or _req(("user", "hi")), model)


async def test_the_routed_model_is_the_one_sent() -> None:
    handler = answering(200, message())
    await _call(handler, model="claude-sonnet-5-5")
    assert handler.body["model"] == "claude-sonnet-5-5"


def test_a_rejection_is_still_a_provider_error() -> None:
    # Code that handles every ProviderError (retries, fallbacks) must see rejections too.
    assert issubclass(ProviderRejectedRequest, ProviderError)


async def test_request_body_is_exactly_what_the_api_needs() -> None:
    handler = answering(200, message())
    request = _req(
        ("system", "Be brief."),
        ("system", "Use English."),
        ("user", "hi"),
        ("assistant", "hello"),
        ("system", "From now on, answer in French."),
        ("user", "again"),
        max_tokens=321,
    )
    await _call(handler, request)

    sent = handler.requests[-1]
    assert (sent.method, sent.url.path) == ("POST", "/v1/messages")
    # Exact equality: no temperature/top_p/top_k, no thinking config, nothing extra.
    assert handler.body == {
        "model": MODEL,
        "max_tokens": 321,
        "system": "Be brief.\n\nUse English.",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "From now on, answer in French."},
            {"role": "user", "content": "again"},
        ],
    }


async def test_no_system_field_without_leading_system_messages() -> None:
    handler = answering(200, message())
    await _call(handler, _req(("user", "hi"), ("system", "late note"), ("user", "more")))
    assert "system" not in handler.body
    assert handler.body["messages"][1] == {"role": "system", "content": "late note"}


async def test_api_key_is_sent_only_in_the_authorization_header() -> None:
    # SDK 2.x sends the key as a bearer token (1.x used x-api-key).
    handler = answering(200, message())
    await _call(handler)
    sent = handler.requests[-1]
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent.headers["anthropic-version"] == "2026-10-21"
    others = [v for k, v in sent.headers.items() if k.lower() != "authorization"]
    assert not any(API_KEY in v for v in others)
    assert API_KEY.encode() not in sent.content
    assert API_KEY not in str(sent.url)


async def test_answer_joins_text_blocks_and_drops_thinking() -> None:
    handler = answering(200, message(model="claude-opus-5-5-20261103"))
    res = await _call(handler)
    assert res.content == "Hello world"
    assert "private reasoning" not in res.content
    assert (res.provider, res.model) == ("anthropic", "claude-opus-5-5-20261103")
    assert res.usage == Usage(input_tokens=12, output_tokens=7)
    assert res.stop_reason == "end_turn"


async def test_other_stop_reasons_pass_through() -> None:
    res = await _call(answering(200, message(stop_reason="max_tokens")))
    assert res.stop_reason == "max_tokens"


async def test_missing_stop_reason_defaults_to_end_turn() -> None:
    res = await _call(answering(200, message(stop_reason=None)))
    assert res.stop_reason == "end_turn"


async def test_refusal_keeps_its_usage_and_stop_reason() -> None:
    body = message(content=[], stop_reason="refusal", input_tokens=40, output_tokens=3)
    res = await _call(answering(200, body))
    assert res.stop_reason == "refusal"
    assert res.content == ""
    assert res.usage == Usage(input_tokens=40, output_tokens=3)


def _carries_nothing_upstream(exc: BaseException) -> None:
    """No upstream text in the message, and no SDK exception chained behind it."""
    assert UPSTREAM_SECRET not in str(exc)
    assert API_KEY not in str(exc)
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.parametrize("status", [400, 422])
async def test_request_errors_are_rejections(status: int) -> None:
    with pytest.raises(ProviderRejectedRequest) as caught:
        await _call(answering(status, error_body(status)))
    _carries_nothing_upstream(caught.value)


@pytest.mark.parametrize("status", [401, 402, 403, 404, 409, 413, 429, 500, 503, 529])
async def test_other_statuses_are_provider_errors(status: int) -> None:
    with pytest.raises(ProviderError) as caught:
        await _call(answering(status, error_body(status)))
    assert not isinstance(caught.value, ProviderRejectedRequest)
    _carries_nothing_upstream(caught.value)


@pytest.mark.parametrize(
    "exc", [httpx2.ConnectError(UPSTREAM_SECRET), httpx2.ReadTimeout(UPSTREAM_SECRET)]
)
async def test_transport_failures_are_provider_errors(exc: Exception) -> None:
    with pytest.raises(ProviderError) as caught:
        await _call(raising(exc))
    assert not isinstance(caught.value, ProviderRejectedRequest)
    _carries_nothing_upstream(caught.value)


def _json_200(raw: bytes) -> Recorder:
    headers = {"content-type": "application/json"}
    return Recorder(lambda _r: httpx2.Response(200, content=raw, headers=headers))


def _without(field: str) -> bytes:
    body = message()
    del body[field]
    return json.dumps(body).encode()


@pytest.mark.parametrize(
    "handler",
    [
        Recorder(lambda _r: httpx2.Response(200, content=b"not json")),  # SDK returns a str
        _json_200(b"{not json " + UPSTREAM_SECRET.encode()),
        _json_200(b"null"),
        _json_200(b"[]"),
        _json_200(json.dumps(error_body(500)).encode()),  # an error shaped like a 200
        _json_200(_without("usage")),
        _json_200(_without("content")),
    ],
    ids=["text", "bad-json", "null", "list", "error-shaped", "no-usage", "no-content"],
)
async def test_a_malformed_answer_is_a_provider_error(handler: Recorder) -> None:
    with pytest.raises(ProviderError) as caught:
        await _call(handler)
    assert not isinstance(caught.value, ProviderRejectedRequest)
    _carries_nothing_upstream(caught.value)


async def test_other_block_types_are_dropped() -> None:
    content = [
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "a"},
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}},
        {"type": "some_future_block", "payload": 1},
        {"type": "text", "text": "b"},
    ]
    res = await _call(answering(200, message(content=content)))
    assert res.content == "ab"


async def test_cached_tokens_are_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    body = message()
    body["usage"] |= {"cache_read_input_tokens": 1000, "cache_creation_input_tokens": 500}
    with caplog.at_level(logging.ERROR, logger="llm_gateway.providers.anthropic"):
        res = await _call(answering(200, body))
    assert res.usage == Usage(input_tokens=12, output_tokens=7)
    assert "anthropic reported 1500 cached input tokens that are not billed" in caplog.text


async def test_no_cached_tokens_no_log(caplog: pytest.LogCaptureFixture) -> None:
    body = message()
    body["usage"] |= {"cache_read_input_tokens": 0, "cache_creation_input_tokens": None}
    with caplog.at_level(logging.ERROR, logger="llm_gateway.providers.anthropic"):
        await _call(answering(200, body))
    assert caplog.text == ""


async def test_a_conversation_may_start_with_the_assistant() -> None:
    handler = answering(200, message())
    await _call(handler, _req(("assistant", "Hi, how can I help?"), ("system", "s"), ("user", "u")))
    assert "system" not in handler.body
    assert handler.body["messages"] == [
        {"role": "assistant", "content": "Hi, how can I help?"},
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
