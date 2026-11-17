import asyncio
import concurrent.futures
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from llm_gateway.billing.pricing import Price
from llm_gateway.config import Settings
from llm_gateway.main import create_app
from llm_gateway.providers.base import Provider
from llm_gateway.providers.fake import FakeProvider
from tests.conftest import MutableClock
from tests.helpers import (
    ADMIN_TOKEN,
    HI,
    RaisingProvider,
    SpyProvider,
    audit_rows,
    auth,
    bootstrap,
    create_key,
    find_key_id,
)

_MINUTE = timedelta(minutes=1)


def dev_client(client: TestClient) -> tuple[str, str, str]:
    tenant, admin = bootstrap(client)
    return tenant, admin, create_key(client, admin, "developer")


def test_chat_answers_audits_and_bills(client: TestClient, fixed_now: datetime) -> None:
    tenant, admin, dev = dev_client(client)
    r = client.post("/v1/chat", headers=auth(dev), json=HI)
    assert r.status_code == 200
    assert r.json()["content"] == "echo: hi" and r.json()["cost_usd"] == "0.000005"
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.provider, rec.model) == (
        "ok",
        None,
        "fake",
        "fake-echo",
    )
    assert (rec.input_tokens, rec.output_tokens, rec.cost_usd) == (1, 2, Decimal("0.000005"))
    assert (
        rec.key_prefix == dev.split("_")[1] and rec.created_at == fixed_now and rec.latency_ms >= 0
    )
    assert rec.key_id == find_key_id(client, admin, dev)


def test_clock_is_read_once_per_request(settings: Settings) -> None:
    # A clock that moves on every read: routing, pricing and the audit row must all use
    # the first reading, or a call at midnight could be priced on one day and logged on another.
    readings: list[datetime] = []

    def ticking() -> datetime:
        readings.append(datetime(2026, 11, 17, 12, 0, tzinfo=UTC) + len(readings) * _MINUTE)
        return readings[-1]

    client = TestClient(create_app(settings, clock=ticking))
    tenant, _, dev = dev_client(client)
    before = len(readings)
    client.post("/v1/chat", headers=auth(dev), json=HI)
    [rec] = audit_rows(client, tenant)
    assert rec.created_at == readings[before]
    assert len(readings) == before + 1


def test_latency_is_measured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_gateway.api.chat as chat_module

    tenant, _, dev = dev_client(client)
    ticks = iter([10.0, 10.25])
    # Patch only the route's own reference: the global time.perf_counter is used by
    # anyio and httpx too.
    monkeypatch.setattr(chat_module, "perf_counter", lambda: next(ticks))
    client.post("/v1/chat", headers=auth(dev), json=HI)
    [rec] = audit_rows(client, tenant)
    assert rec.latency_ms == 250


def test_cancelled_call_is_audited(client: TestClient) -> None:
    tenant, _, dev = dev_client(client)
    client.app.state.gateway.providers["fake"] = RaisingProvider(asyncio.CancelledError())  # type: ignore[attr-defined]
    with pytest.raises(concurrent.futures.CancelledError):  # TestClient surfaces it as this class
        client.post("/v1/chat", headers=auth(dev), json=HI)
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.cost_usd) == ("error", "cancelled", 0)


async def test_shield_keeps_the_audit_row_on_real_cancellation(settings: Settings) -> None:
    # TestClient can't cancel a request mid-flight, so call the route directly inside a
    # task group and cancel it while the provider hangs. The audit repo really awaits:
    # without the shield, that await is cancelled too and the row is lost.
    import anyio

    from llm_gateway.api.chat import chat as chat_route
    from llm_gateway.audit.records import AuditRecord, InMemoryAuditRepository
    from llm_gateway.auth.deps import Principal
    from llm_gateway.auth.keys import Role
    from llm_gateway.providers.base import ChatMessage, ChatRequest, ChatResponse
    from llm_gateway.state import AppState

    class AwaitingAudit(InMemoryAuditRepository):
        async def add(self, record: AuditRecord) -> None:
            await anyio.sleep(0)
            await super().add(record)

    started = anyio.Event()

    class HangingProvider:
        name = "fake"

        async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
            started.set()
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    state = AppState(settings=settings, clock=lambda: now)
    state.audit = AwaitingAudit()
    state.providers["fake"] = HangingProvider()
    caller = Principal(tenant_id="t1", key_id="k1", key_prefix="p1", role=Role.DEVELOPER)
    body = ChatRequest(messages=[ChatMessage(role="user", content="hi")])

    after_route: list[str] = []

    async def run() -> None:
        await chat_route(body, state, caller)
        after_route.append("route returned instead of re-raising the cancellation")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        await started.wait()
        tg.cancel_scope.cancel()

    rows = await state.audit.list_page("t1", now, now + _MINUTE, limit=10, after=None)
    assert [(r.status, r.error_code, r.cost_usd) for r in rows] == [("error", "cancelled", 0)]
    assert after_route == []  # the cancellation was re-raised, not swallowed


def test_unexpected_error_is_500_audited_and_hidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, _, dev = dev_client(client)

    def boom(model: str, on: date) -> Price:
        raise RuntimeError("boom-detail")

    monkeypatch.setattr(client.app.state.gateway.prices, "price_for", boom)  # type: ignore[attr-defined]
    r = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/chat", headers=auth(dev), json=HI
    )
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "boom-detail" not in r.text
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code) == ("error", "internal_error")


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_only_developers_chat(client: TestClient, role: str) -> None:
    tenant, admin = bootstrap(client)
    key = admin if role == "admin" else create_key(client, admin, role)
    assert client.post("/v1/chat", headers=auth(key), json=HI).status_code == 403
    assert audit_rows(client, tenant) == []  # 403 writes no audit row


def test_blank_content_is_422_through_api(client: TestClient) -> None:
    tenant, _, dev = dev_client(client)
    r = client.post(
        "/v1/chat", headers=auth(dev), json={"messages": [{"role": "user", "content": "   "}]}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert audit_rows(client, tenant) == []  # 422 writes no audit row


@pytest.mark.parametrize(
    "provider", [FakeProvider(fail=True), RaisingProvider(RuntimeError("boom"))]
)
def test_provider_failure_is_audited_not_billed(client: TestClient, provider: Provider) -> None:
    tenant, _, dev = dev_client(client)
    client.app.state.gateway.providers["fake"] = provider  # type: ignore[attr-defined]
    r = client.post("/v1/chat", headers=auth(dev), json=HI)
    assert r.status_code == 502 and r.json()["error"]["code"] == "provider_error"
    assert "boom" not in r.text and "Traceback" not in r.text
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.cost_usd, rec.input_tokens) == (
        "error",
        "provider_error",
        0,
        0,
    )
    assert (rec.provider, rec.model, rec.output_tokens) == ("fake", "fake-echo", 0)
    assert str(rec.cost_usd) == "0.000000"


def test_unknown_model_is_400_and_audited(client: TestClient) -> None:
    tenant, _, dev = dev_client(client)
    spy = SpyProvider()
    client.app.state.gateway.providers["fake"] = spy  # type: ignore[attr-defined]
    r = client.post("/v1/chat", headers=auth(dev), json={"model": "gpt-nope", **HI})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.provider, rec.model) == (
        "error",
        "unknown_model",
        "",
        "gpt-nope",
    )
    assert spy.calls == 0


def test_stale_routing_rule_is_audited_with_the_rule_model(client: TestClient) -> None:
    tenant, admin, dev = dev_client(client)
    state = client.app.state.gateway  # type: ignore[attr-defined]
    state.catalog["fake-big"] = "fake"
    client.put(
        "/v1/admin/routing", headers=auth(admin), json={"mode": "mandatory", "model": "fake-big"}
    )
    del state.catalog["fake-big"]  # the rule now names a model that no longer exists
    spy = SpyProvider()
    state.providers["fake"] = spy
    r = client.post("/v1/chat", headers=auth(dev), json={"model": "fake-echo", **HI})
    assert r.status_code == 400 and r.json()["error"]["code"] == "routing_rule_invalid"
    [rec] = audit_rows(client, tenant)
    assert (rec.error_code, rec.provider, rec.model) == ("routing_rule_invalid", "", "fake-big")
    assert spy.calls == 0


def test_catalog_provider_missing_is_not_a_500(client: TestClient) -> None:
    tenant, _, dev = dev_client(client)
    client.app.state.gateway.catalog["ghost-model"] = "ghost-provider"  # type: ignore[attr-defined]
    r = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/chat", headers=auth(dev), json={"model": "ghost-model", **HI}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"
    [rec] = audit_rows(client, tenant)
    assert (rec.error_code, rec.provider, rec.model) == ("unknown_model", "", "ghost-model")


def test_default_model_comes_from_settings(clock: MutableClock) -> None:
    app = create_app(
        Settings(_env_file=None, admin_token=ADMIN_TOKEN, default_model="missing"), clock=clock
    )
    client = TestClient(app)
    _, _, dev = dev_client(client)
    r = client.post("/v1/chat", headers=auth(dev), json=HI)
    assert r.json()["error"]["code"] == "unknown_model"


def test_missing_price_never_reaches_provider(client: TestClient, clock: MutableClock) -> None:
    clock.set(datetime(2025, 6, 1, tzinfo=UTC))
    tenant, _, dev = dev_client(client)
    spy = SpyProvider()
    client.app.state.gateway.providers["fake"] = spy  # type: ignore[attr-defined]
    r = client.post("/v1/chat", headers=auth(dev), json=HI)
    assert r.status_code == 503 and r.json()["error"]["code"] == "pricing_unavailable"
    assert spy.calls == 0
    [rec] = audit_rows(client, tenant)
    assert (rec.status, rec.error_code, rec.cost_usd) == ("error", "pricing_unavailable", 0)


def test_price_change_bills_each_call_at_its_date(client: TestClient, clock: MutableClock) -> None:
    tenant, _, dev = dev_client(client)
    client.app.state.gateway.prices.add(  # type: ignore[attr-defined]
        Price("fake-echo", date(2026, 11, 18), Decimal("3.00"), Decimal("6.00"))
    )
    client.post("/v1/chat", headers=auth(dev), json=HI)
    clock.set(datetime(2026, 11, 18, 9, 0, tzinfo=UTC))
    client.post("/v1/chat", headers=auth(dev), json=HI)
    assert [r.cost_usd for r in audit_rows(client, tenant)] == [
        Decimal("0.000005"),
        Decimal("0.000015"),
    ]


def test_routing_rule_applies_per_tenant_and_can_be_cleared(client: TestClient) -> None:
    state = client.app.state.gateway  # type: ignore[attr-defined]
    state.catalog["fake-big"] = "fake"
    state.prices.add(Price("fake-big", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00")))
    _, admin_a = bootstrap(client, "a")
    _, admin_b = bootstrap(client, "b")
    dev_a, dev_b = (
        create_key(client, admin_a, "developer"),
        create_key(client, admin_b, "developer"),
    )
    client.put(
        "/v1/admin/routing", headers=auth(admin_a), json={"mode": "mandatory", "model": "fake-big"}
    )

    def chat(key: str) -> str:
        r = client.post("/v1/chat", headers=auth(key), json={"model": "fake-echo", **HI})
        model: str = r.json()["model"]
        return model

    assert chat(dev_a) == "fake-big"
    assert chat(dev_b) == "fake-echo"
    assert client.delete("/v1/admin/routing", headers=auth(admin_a)).status_code == 204
    assert chat(dev_a) == "fake-echo"
