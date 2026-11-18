"""The provider timeout: a call that takes too long is cut off, audited and not billed."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anyio
import pytest
from fastapi.testclient import TestClient

import llm_gateway.api.chat as chat_module
from llm_gateway.api.errors import ApiError
from llm_gateway.audit.records import AuditRecord, InMemoryAuditRepository
from llm_gateway.auth.deps import Principal
from llm_gateway.auth.keys import Role
from llm_gateway.config import Settings
from llm_gateway.main import create_app
from llm_gateway.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderRejectedRequest,
)
from llm_gateway.providers.fake import FakeProvider
from llm_gateway.state import AppState
from tests.conftest import MutableClock
from tests.helpers import HI, RaisingProvider, audit_rows, auth, bootstrap, create_key

TIMEOUT_S = 0.2


class SlowProvider:
    """Answers like the fake provider, after ``delay`` seconds."""

    name = "fake"

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.finished = False

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        await anyio.sleep(self.delay)
        self.finished = True
        return await FakeProvider().chat(request, model)


class SlowAudit(InMemoryAuditRepository):
    """Takes longer to write than the provider timeout."""

    async def add(self, record: AuditRecord) -> None:
        await anyio.sleep(TIMEOUT_S * 2)
        await super().add(record)


@pytest.fixture
def quick(settings: Settings, clock: MutableClock) -> Iterator[TestClient]:
    # Below the 1 s floor the setting allows, so set it past validation: tests only.
    settings.provider_timeout_s = TIMEOUT_S
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def dev(client: TestClient) -> tuple[str, str]:
    tenant, admin = bootstrap(client)
    return tenant, create_key(client, admin, "developer")


def use(client: TestClient, provider: object) -> None:
    client.app.state.gateway.providers["fake"] = provider  # type: ignore[attr-defined]


def test_a_hung_provider_is_cut_off_with_504(
    quick: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, key = dev(quick)
    provider = SlowProvider(delay=30)
    use(quick, provider)
    with caplog.at_level(logging.WARNING, logger="llm_gateway.chat"):
        r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 504
    assert r.json()["error"] == {
        "code": "provider_timeout",
        "message": "the model provider did not answer in time",
    }
    assert not provider.finished
    assert "provider fake timed out for model fake-echo after 0.2 s" in caplog.text
    [row] = audit_rows(quick, tenant)
    assert (row.status, row.error_code) == ("error", "provider_timeout")
    assert (row.provider, row.model) == ("fake", "fake-echo")
    assert (row.input_tokens, row.output_tokens, row.cost_usd) == (0, 0, Decimal(0))
    # Loose low end: on Windows the event loop clock ticks every ~15.6 ms and timers may
    # fire up to one tick early. The high end catches a deadline that is far too long.
    assert TIMEOUT_S * 1000 - 50 <= row.latency_ms < 2000


def test_an_answer_inside_the_timeout_is_billed(quick: TestClient) -> None:
    tenant, key = dev(quick)
    use(quick, SlowProvider(delay=TIMEOUT_S / 4))
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 200, r.text
    [row] = audit_rows(quick, tenant)
    assert (row.status, row.cost_usd) == ("ok", Decimal("0.000005"))


def test_the_timeout_does_not_cut_the_audit_write(quick: TestClient) -> None:
    # The audit write takes longer than the provider timeout; it must still complete.
    tenant, key = dev(quick)
    quick.app.state.gateway.audit = SlowAudit()  # type: ignore[attr-defined]
    use(quick, SlowProvider(delay=30))
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 504
    [row] = audit_rows(quick, tenant)
    assert row.error_code == "provider_timeout"


def test_slow_audit_after_a_fast_answer_is_still_ok(quick: TestClient) -> None:
    tenant, key = dev(quick)
    quick.app.state.gateway.audit = SlowAudit()  # type: ignore[attr-defined]
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 200, r.text
    [row] = audit_rows(quick, tenant)
    assert row.status == "ok"


def test_a_providers_own_timeout_error_is_a_502(quick: TestClient) -> None:
    # Only the gateway's own deadline is a 504; a provider raising TimeoutError is a failure.
    tenant, key = dev(quick)
    use(quick, RaisingProvider(TimeoutError("upstream read timed out")))
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "provider_error"
    [row] = audit_rows(quick, tenant)
    assert row.error_code == "provider_error"


def test_the_configured_timeout_is_the_one_used(settings: Settings, clock: MutableClock) -> None:
    settings.provider_timeout_s = TIMEOUT_S * 10
    with TestClient(create_app(settings, clock=clock)) as client:
        _, key = dev(client)
        use(client, SlowProvider(delay=TIMEOUT_S * 2))  # longer than 0.2 s, under 2 s
        r = client.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 200, r.text


class TurnsCancellationIntoAnError:
    """Like an HTTP client that reports an aborted request as a connection error."""

    name = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("connection aborted")

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            raise self.error from None
        raise AssertionError("unreachable")


def test_an_error_caused_by_the_deadline_is_still_a_timeout(quick: TestClient) -> None:
    tenant, key = dev(quick)
    use(quick, TurnsCancellationIntoAnError())
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 504
    [row] = audit_rows(quick, tenant)
    assert (row.error_code, row.cost_usd) == ("provider_timeout", Decimal(0))


class AnswersDespiteTheDeadline:
    """Ignores the cancellation and answers anyway (the provider did charge for it)."""

    name = "fake"

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            pass
        return await FakeProvider().chat(request, model)


def test_an_answer_that_arrives_anyway_is_billed(quick: TestClient) -> None:
    # Once there is an answer, the tenant gets it and pays for it: never an answered
    # call reported as a free timeout.
    tenant, key = dev(quick)
    use(quick, AnswersDespiteTheDeadline())
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 200, r.text
    [row] = audit_rows(quick, tenant)
    assert (row.status, row.cost_usd) == ("ok", Decimal("0.000005"))


def test_a_rejection_caused_by_the_deadline_is_still_a_timeout(quick: TestClient) -> None:
    tenant, key = dev(quick)
    use(quick, TurnsCancellationIntoAnError(ProviderRejectedRequest("aborted")))
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 504
    [row] = audit_rows(quick, tenant)
    assert row.error_code == "provider_timeout"


def test_a_call_just_over_the_deadline_is_cut_off(quick: TestClient) -> None:
    # Pins the deadline's size from above: 1.75x the timeout must not get through.
    _, key = dev(quick)
    use(quick, SlowProvider(delay=TIMEOUT_S * 1.75))
    r = quick.post("/v1/chat", headers=auth(key), json=HI)
    assert r.status_code == 504


def _direct(settings: Settings, provider: object) -> tuple[AppState, Principal, ChatRequest]:
    # Called without TestClient so the raised exception itself can be inspected.
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    state = AppState(settings=settings, clock=lambda: now)
    state.providers["fake"] = provider  # type: ignore[assignment]
    caller = Principal(tenant_id="t1", key_id="k1", key_prefix="p1", role=Role.DEVELOPER)
    body = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
    return state, caller, body


async def _rows(state: AppState) -> list[AuditRecord]:
    start = datetime(2026, 11, 17, tzinfo=UTC)
    return await state.audit.list_page("t1", start, start + timedelta(days=1), limit=10, after=None)


async def test_the_504_carries_no_provider_context(settings: Settings) -> None:
    settings.provider_timeout_s = TIMEOUT_S
    state, caller, body = _direct(settings, TurnsCancellationIntoAnError())
    with pytest.raises(ApiError) as caught:
        await chat_module.chat(body, state, caller)
    assert caught.value.code == "provider_timeout"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_an_outer_cancel_hidden_by_the_provider_is_not_lost(settings: Settings) -> None:
    # The client goes away (outer cancel) long before the deadline; the provider turns the
    # cancellation into an ordinary error. It must still end as a cancellation.
    settings.provider_timeout_s = 5
    state, caller, body = _direct(settings, TurnsCancellationIntoAnError())
    with anyio.move_on_after(TIMEOUT_S / 4) as outer:
        await chat_module.chat(body, state, caller)
    assert outer.cancelled_caught
    [row] = await _rows(state)
    assert (row.status, row.error_code, row.cost_usd) == ("error", "cancelled", Decimal(0))
