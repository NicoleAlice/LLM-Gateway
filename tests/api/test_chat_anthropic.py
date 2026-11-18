"""``/v1/chat`` through the real ``AnthropicProvider`` and SDK, over a mock transport."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import llm_gateway.api.chat as chat_module
from llm_gateway.api.errors import ApiError
from llm_gateway.auth.deps import Principal
from llm_gateway.auth.keys import Role
from llm_gateway.billing.pricing import Price
from llm_gateway.config import Settings
from llm_gateway.providers.anthropic import AnthropicProvider
from llm_gateway.providers.base import ChatMessage, ChatRequest
from llm_gateway.state import AppState
from tests.anthropic_mock import (
    UPSTREAM_SECRET,
    Recorder,
    answering,
    client_for,
    error_body,
    message,
)
from tests.helpers import audit_rows, auth, bootstrap, create_key

MODEL = "claude-opus-5-5"
ASK = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}


def with_claude(client: TestClient, handler: Recorder) -> tuple[str, str]:
    """Register the provider, model and its price; return ``(tenant_id, developer_key)``."""
    state: AppState = client.app.state.gateway  # type: ignore[attr-defined]
    state.providers["anthropic"] = AnthropicProvider(client_for(handler))
    state.catalog[MODEL] = "anthropic"
    state.prices.add(Price(MODEL, date(2026, 11, 3), Decimal("4"), Decimal("20")))
    tenant, admin = bootstrap(client)
    return tenant, create_key(client, admin, "developer")


def test_answer_is_billed_at_the_model_price(client: TestClient) -> None:
    tenant, dev = with_claude(client, answering(200, message()))
    r = client.post("/v1/chat", headers=auth(dev), json=ASK)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "Hello world"
    assert body["stop_reason"] == "end_turn"
    assert (body["provider"], body["model"]) == ("anthropic", MODEL)
    # 12 input tokens x $4/MTok + 7 output tokens x $20/MTok
    assert body["cost_usd"] == "0.000188"
    [row] = audit_rows(client, tenant)
    assert (row.status, row.provider, row.model) == ("ok", "anthropic", MODEL)
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (12, 7, Decimal("0.000188"))


def test_refusal_is_billed_and_visible(client: TestClient) -> None:
    body = message(content=[], stop_reason="refusal", input_tokens=40, output_tokens=3)
    tenant, dev = with_claude(client, answering(200, body))
    r = client.post("/v1/chat", headers=auth(dev), json=ASK)
    assert r.status_code == 200, r.text
    assert r.json()["stop_reason"] == "refusal"
    assert r.json()["cost_usd"] == "0.000220"
    [row] = audit_rows(client, tenant)
    assert (row.status, row.cost_usd) == ("ok", Decimal("0.000220"))


def test_fake_provider_answers_report_end_turn(client: TestClient) -> None:
    _tenant, admin = bootstrap(client)
    dev = create_key(client, admin, "developer")
    r = client.post("/v1/chat", headers=auth(dev), json={"messages": ASK["messages"]})
    assert r.json()["stop_reason"] == "end_turn"


def test_rejected_request_is_400_and_not_billed(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, dev = with_claude(client, answering(400, error_body(400)))
    with caplog.at_level(logging.WARNING, logger="llm_gateway.chat"):
        r = client.post("/v1/chat", headers=auth(dev), json=ASK)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "provider_rejected_request"
    assert UPSTREAM_SECRET not in r.text
    assert UPSTREAM_SECRET not in caplog.text
    assert (
        "provider anthropic rejected a request for model claude-opus-5-5: ProviderRejectedRequest"
        in caplog.text
    )
    [row] = audit_rows(client, tenant)
    assert (row.status, row.error_code) == ("error", "provider_rejected_request")
    assert (row.provider, row.model, row.cost_usd) == ("anthropic", MODEL, Decimal(0))


@pytest.mark.parametrize("status", [429, 500, 529])
def test_upstream_failure_is_502_and_not_billed(
    client: TestClient, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    tenant, dev = with_claude(client, answering(status, error_body(status)))
    with caplog.at_level(logging.WARNING, logger="llm_gateway.chat"):
        r = client.post("/v1/chat", headers=auth(dev), json=ASK)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "provider_error"
    assert UPSTREAM_SECRET not in r.text
    assert UPSTREAM_SECRET not in caplog.text
    [row] = audit_rows(client, tenant)
    assert (row.status, row.error_code, row.cost_usd) == ("error", "provider_error", Decimal(0))


@pytest.mark.parametrize(
    ("status", "code"), [(400, "provider_rejected_request"), (529, "provider_error")]
)
async def test_route_errors_carry_no_upstream_context(
    settings: Settings, status: int, code: str
) -> None:
    # Called directly so the raised ApiError itself can be inspected: anything that walks
    # __context__ (an error tracker, logger.exception) must not reach the SDK exception.
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    state = AppState(settings=settings, clock=lambda: now)
    state.providers["anthropic"] = AnthropicProvider(
        client_for(answering(status, error_body(status)))
    )
    state.catalog[MODEL] = "anthropic"
    state.prices.add(Price(MODEL, date(2026, 11, 3), Decimal("4"), Decimal("20")))
    caller = Principal(tenant_id="t1", key_id="k1", key_prefix="p1", role=Role.DEVELOPER)
    body = ChatRequest(model=MODEL, messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(ApiError) as caught:
        await chat_module.chat(body, state, caller)
    assert caught.value.code == code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
