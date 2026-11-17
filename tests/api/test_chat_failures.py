"""Failure paths found by the Task 7 review: half-finished calls, a broken audit store,
and providers that misbehave."""

import asyncio
import concurrent.futures
import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import llm_gateway.api.chat as chat_module
from llm_gateway.audit.records import AuditRecord, InMemoryAuditRepository
from llm_gateway.auth.deps import Principal
from llm_gateway.auth.keys import Role
from llm_gateway.config import Settings
from llm_gateway.providers.base import ChatMessage, ChatRequest, ChatResponse, Usage
from llm_gateway.state import AppState
from tests.helpers import HI, RaisingProvider, audit_rows, auth, bootstrap, create_key


def dev_client(client: TestClient) -> tuple[str, str]:
    tenant, admin = bootstrap(client)
    return tenant, create_key(client, admin, "developer")


class FailingAudit(InMemoryAuditRepository):
    async def add(self, record: AuditRecord) -> None:
        raise RuntimeError("db down")


class HangingAudit(InMemoryAuditRepository):
    async def add(self, record: AuditRecord) -> None:
        await asyncio.sleep(3600)


class JunkProvider:
    """Returns a response that skipped validation (as a buggy provider might)."""

    name = "fake"

    def __init__(self, response: ChatResponse) -> None:
        self.response = response

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        return self.response


def test_failure_after_the_answer_is_not_billed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, dev = dev_client(client)

    class Exploding:
        def __init__(self, **kwargs: object) -> None:
            raise RuntimeError("could not build the response")

    monkeypatch.setattr(chat_module, "ChatResult", Exploding)
    r = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/chat", headers=auth(dev), json=HI
    )
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.cost_usd, rec.input_tokens) == (
        "error",
        "internal_error",
        0,
        0,
    )


@pytest.mark.parametrize(
    "response",
    [
        ChatResponse.model_construct(
            provider="fake",
            model="fake-echo",
            content="hi",
            usage=Usage.model_construct(input_tokens=-5_000_000, output_tokens=0),
        ),
        ChatResponse.model_construct(
            provider="fake",
            model="fake-echo",
            content=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        ),
    ],
    ids=["negative-tokens", "missing-content"],
)
def test_invalid_provider_response_is_502_and_not_billed(
    client: TestClient, response: ChatResponse
) -> None:
    tenant, dev = dev_client(client)
    client.app.state.gateway.providers["fake"] = JunkProvider(response)  # type: ignore[attr-defined]
    r = client.post("/v1/chat", headers=auth(dev), json=HI)
    assert r.status_code == 502 and r.json()["error"]["code"] == "provider_error"
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.cost_usd) == ("error", 0)


def test_provider_failure_is_logged_without_its_message(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _, dev = dev_client(client)
    secretish = "user prompt echoed by upstream"
    client.app.state.gateway.providers["fake"] = RaisingProvider(RuntimeError(secretish))  # type: ignore[attr-defined]
    with caplog.at_level(logging.WARNING, logger="llm_gateway.chat"):
        client.post("/v1/chat", headers=auth(dev), json=HI)
    assert "provider fake failed for model fake-echo: RuntimeError" in caplog.text
    assert secretish not in caplog.text


def test_answered_call_fails_closed_if_audit_cannot_be_written(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _, dev = dev_client(client)
    client.app.state.gateway.audit = FailingAudit()  # type: ignore[attr-defined]
    with caplog.at_level(logging.ERROR, logger="llm_gateway.chat"):
        r = TestClient(client.app, raise_server_exceptions=False).post(
            "/v1/chat", headers=auth(dev), json=HI
        )
    assert r.status_code == 500 and r.json()["error"]["code"] == "audit_unavailable"
    assert "echo: hi" not in r.text  # the unrecorded answer is not handed out
    assert "audit write failed" in caplog.text and "status=ok" in caplog.text
    assert "cost=0.000005" in caplog.text  # enough to reconcile the bill


def test_audit_failure_keeps_the_original_error(client: TestClient) -> None:
    _, dev = dev_client(client)
    client.app.state.gateway.audit = FailingAudit()  # type: ignore[attr-defined]
    r = client.post("/v1/chat", headers=auth(dev), json={"model": "gpt-nope", **HI})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"


def test_audit_failure_keeps_the_cancellation(client: TestClient) -> None:
    _, dev = dev_client(client)
    state = client.app.state.gateway  # type: ignore[attr-defined]
    state.audit = FailingAudit()
    state.providers["fake"] = RaisingProvider(asyncio.CancelledError())
    with pytest.raises(concurrent.futures.CancelledError):
        client.post("/v1/chat", headers=auth(dev), json=HI)


def test_hung_audit_store_is_bounded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, dev = dev_client(client)
    monkeypatch.setattr(chat_module, "AUDIT_WRITE_TIMEOUT_S", 0.05)
    client.app.state.gateway.audit = HangingAudit()  # type: ignore[attr-defined]
    with caplog.at_level(logging.ERROR, logger="llm_gateway.chat"):
        r = TestClient(client.app, raise_server_exceptions=False).post(
            "/v1/chat", headers=auth(dev), json=HI
        )
    assert r.status_code == 500 and r.json()["error"]["code"] == "audit_unavailable"
    assert "audit write timed out" in caplog.text


class Interrupt(BaseException):
    """A BaseException that is neither Exception nor CancelledError."""


async def test_other_base_exceptions_still_get_an_error_code(settings: Settings) -> None:
    # Called directly: TestClient turns a foreign BaseException into a cancellation.
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    state = AppState(settings=settings, clock=lambda: now)
    state.providers["fake"] = RaisingProvider(Interrupt())
    caller = Principal(tenant_id="t1", key_id="k1", key_prefix="p1", role=Role.DEVELOPER)
    body = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(Interrupt):
        await chat_module.chat(body, state, caller)
    [rec] = await state.audit.list_for_tenant("t1", now, now + timedelta(minutes=1))
    assert (rec.status, rec.error_code) == ("error", "internal_error")
