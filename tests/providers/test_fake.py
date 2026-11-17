from typing import Any

import pytest
from pydantic import ValidationError

from llm_gateway.providers.base import ChatMessage, ChatRequest, ProviderError, Usage
from llm_gateway.providers.fake import FakeProvider


def _req(*pairs: tuple[str, str], **kw: Any) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role=r, content=c) for r, c in pairs], **kw)  # type: ignore[arg-type]


async def test_fake_echoes_last_user_message() -> None:
    res = await FakeProvider().chat(_req(("user", "hello there")), model="fake-echo")
    assert res.content == "echo: hello there"
    assert (res.provider, res.model) == ("fake", "fake-echo")
    assert res.usage == Usage(input_tokens=2, output_tokens=3)


async def test_fake_uses_last_user_message_and_counts_all() -> None:
    req = _req(("system", "be nice"), ("user", "first q"), ("assistant", "a"), ("user", "second"))
    res = await FakeProvider().chat(req, model="fake-echo")
    assert res.content == "echo: second"
    assert res.usage == Usage(input_tokens=6, output_tokens=2)


async def test_fake_can_fail_on_demand() -> None:
    with pytest.raises(ProviderError):
        await FakeProvider(fail=True).chat(_req(("user", "hi")), model="fake-echo")


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "user", "content": "   "}],
        [{"role": "system", "content": "only system"}],
    ],
)
def test_request_rejects_invalid_messages(messages: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"messages": messages})


def test_content_is_not_rewritten() -> None:
    assert _req(("user", "  indented\n")).messages[0].content == "  indented\n"


def test_content_max_length() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="a" * 32_001)


@pytest.mark.parametrize("n", [0, 8193])
def test_max_tokens_bounds(n: int) -> None:
    with pytest.raises(ValidationError):
        _req(("user", "hi"), max_tokens=n)


def test_request_defaults() -> None:
    req = _req(("user", "hi"))
    assert (req.model, req.max_tokens) == (None, 1024)


def test_model_name_max_length() -> None:
    _req(("user", "hi"), model="m" * 100)
    with pytest.raises(ValidationError):
        _req(("user", "hi"), model="m" * 101)


def test_messages_max_count() -> None:
    _req(*[("user", "hi")] * 100)
    with pytest.raises(ValidationError):
        _req(*[("user", "hi")] * 101)


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatMessage.model_validate({"role": "tool", "content": "hi"})


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_usage_rejects_negative_tokens(field: str) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"input_tokens": 0, "output_tokens": 0, field: -1})


@pytest.mark.parametrize("model", ["", "m" * 101])
def test_model_name_must_be_non_empty_and_bounded(model: str) -> None:
    with pytest.raises(ValidationError):
        _req(("user", "hi"), model=model)


@pytest.mark.parametrize(
    "body",
    [
        {"messages": [{"role": "user", "content": "hi"}], "stream": True},
        {"messages": [{"role": "user", "content": "hi", "name": "bob"}]},
    ],
)
def test_unknown_fields_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(body)


@pytest.mark.parametrize("value", ["10", 10.0, True])
def test_max_tokens_must_be_an_integer(value: Any) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": value}
        )


@pytest.mark.parametrize(
    "content", [chr(0x200B), chr(0xFEFF) + chr(0x2060) + " ", chr(0xA0) + chr(0x2003)]
)
def test_invisible_only_content_is_blank(content: str) -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content=content)


def test_requests_are_immutable() -> None:
    req = _req(("user", "hi"))
    with pytest.raises(ValidationError):
        req.max_tokens = -5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        req.messages[0].role = "assistant"  # type: ignore[misc]


async def test_fake_without_user_message_raises_provider_error() -> None:
    bypassed = ChatRequest.model_construct(messages=[ChatMessage(role="system", content="s")])
    with pytest.raises(ProviderError):
        await FakeProvider().chat(bypassed, model="fake-echo")


async def test_fake_reports_the_model_it_was_asked_for() -> None:
    res = await FakeProvider().chat(_req(("user", "hi")), model="fake-big")
    assert res.model == "fake-big"
