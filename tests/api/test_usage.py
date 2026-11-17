from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from llm_gateway.audit.records import AuditRecord
from llm_gateway.billing.pricing import Price
from llm_gateway.config import Settings
from llm_gateway.main import create_app
from llm_gateway.providers.fake import FakeProvider
from tests.helpers import HI, audit_rows, auth, bootstrap, create_key

WINDOW = {"start": "2026-11-17T00:00:00Z", "end": "2026-11-18T00:00:00Z"}
AUDIT_FIELDS = {
    "id",
    "created_at",
    "key_prefix",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "status",
    "error_code",
    "latency_ms",
}


def setup_calls(client: TestClient) -> tuple[str, str, str]:
    _, admin = bootstrap(client)
    dev, viewer = create_key(client, admin, "developer"), create_key(client, admin, "viewer")
    client.post("/v1/chat", headers=auth(dev), json=HI)
    client.post("/v1/chat", headers=auth(dev), json=HI)
    client.app.state.gateway.providers["fake"] = FakeProvider(fail=True)  # type: ignore[attr-defined]
    client.post("/v1/chat", headers=auth(dev), json=HI)
    return admin, dev, viewer


def test_usage_counts_only_successful_calls(client: TestClient) -> None:
    _, _, viewer = setup_calls(client)
    body = client.get("/v1/usage", headers=auth(viewer), params=WINDOW).json()
    assert body == {
        "total_cost_usd": "0.000010",
        "by_model": [
            {
                "model": "fake-echo",
                "calls": 2,
                "input_tokens": 2,
                "output_tokens": 4,
                "cost_usd": "0.000010",
            }
        ],
    }


def test_audit_lists_all_rows_for_tenant(client: TestClient) -> None:
    _, _, viewer = setup_calls(client)
    rows = client.get("/v1/audit", headers=auth(viewer), params=WINDOW).json()
    assert [r["status"] for r in rows] == ["ok", "ok", "error"]
    assert rows[0]["cost_usd"] == "0.000005" and rows[2]["cost_usd"] == "0.000000"


def test_audit_row_shape(client: TestClient) -> None:
    _, dev, viewer = setup_calls(client)
    rows = client.get("/v1/audit", headers=auth(viewer), params=WINDOW).json()
    assert all(set(r) == AUDIT_FIELDS for r in rows)
    ok, _, failed = rows
    assert ok["created_at"] == "2026-11-17T12:00:00Z"
    assert (ok["key_prefix"], ok["provider"], ok["model"]) == (
        dev.split("_")[1],
        "fake",
        "fake-echo",
    )
    assert (ok["input_tokens"], ok["output_tokens"], ok["error_code"]) == (1, 2, None)
    assert (failed["error_code"], failed["input_tokens"]) == ("provider_error", 0)
    assert isinstance(ok["latency_ms"], int)


def test_tenants_are_isolated(client: TestClient) -> None:
    setup_calls(client)
    _, admin_b = bootstrap(client, "b")
    assert client.get("/v1/audit", headers=auth(admin_b), params=WINDOW).json() == []
    usage = client.get("/v1/usage", headers=auth(admin_b), params=WINDOW).json()
    assert usage == {"total_cost_usd": "0.000000", "by_model": []}


def test_window_is_half_open(client: TestClient) -> None:
    _, _, viewer = setup_calls(client)  # calls at 2026-11-17T12:00Z
    before = {"start": "2026-11-16T00:00:00Z", "end": "2026-11-17T12:00:00Z"}
    body = client.get("/v1/usage", headers=auth(viewer), params=before).json()
    assert body == {"total_cost_usd": "0.000000", "by_model": []}
    starting = {"start": "2026-11-17T12:00:00Z", "end": "2026-11-17T12:00:01Z"}
    rows = client.get("/v1/audit", headers=auth(viewer), params=starting).json()
    assert len(rows) == 3


def test_non_utc_offsets_are_honoured(client: TestClient) -> None:
    _, _, viewer = setup_calls(client)  # 12:00Z == 09:00-03:00
    sao_paulo = {"start": "2026-11-17T08:00:00-03:00", "end": "2026-11-17T09:00:00-03:00"}
    assert client.get("/v1/audit", headers=auth(viewer), params=sao_paulo).json() == []
    sao_paulo["end"] = "2026-11-17T09:00:01-03:00"
    assert len(client.get("/v1/audit", headers=auth(viewer), params=sao_paulo).json()) == 3


def test_usage_groups_and_sorts_models(client: TestClient) -> None:
    state = client.app.state.gateway  # type: ignore[attr-defined]
    for name in ("a-model", "z-model"):
        state.catalog[name] = "fake"
        state.prices.add(Price(name, date(2026, 1, 1), Decimal("10.00"), Decimal("10.00")))
    _, admin = bootstrap(client)
    dev = create_key(client, admin, "developer")
    # Call order fake-echo, z-model, a-model: neither it nor its reverse is alphabetical.
    client.post("/v1/chat", headers=auth(dev), json=HI)  # fake-echo: 0.000005
    client.post("/v1/chat", headers=auth(dev), json={"model": "z-model", **HI})  # 3 tok * 10
    client.post("/v1/chat", headers=auth(dev), json={"model": "a-model", **HI})  # 3 tok * 10
    body = client.get("/v1/usage", headers=auth(admin), params=WINDOW).json()
    assert [m["model"] for m in body["by_model"]] == ["a-model", "fake-echo", "z-model"]
    assert body["by_model"][0]["cost_usd"] == "0.000030"
    assert body["total_cost_usd"] == "0.000065"


def test_rows_are_stored_and_shown_in_utc(settings: Settings) -> None:
    # A clock in UTC-3 at 21:30 on the 17th is 00:30 UTC on the 18th: the row is dated and
    # priced on the UTC calendar, and serialized with "Z" like every other row.
    sao_paulo = timezone(timedelta(hours=-3))
    client = TestClient(
        create_app(settings, clock=lambda: datetime(2026, 11, 17, 21, 30, tzinfo=sao_paulo))
    )
    _, admin = bootstrap(client)
    client.app.state.gateway.prices.add(  # type: ignore[attr-defined]
        Price("fake-echo", date(2026, 11, 18), Decimal("3.00"), Decimal("6.00"))
    )
    client.post("/v1/chat", headers=auth(create_key(client, admin, "developer")), json=HI)
    window = {"start": "2026-11-18T00:00:00Z", "end": "2026-11-19T00:00:00Z"}
    [row] = client.get("/v1/audit", headers=auth(admin), params=window).json()
    assert row["created_at"] == "2026-11-18T00:30:00Z"
    assert row["cost_usd"] == "0.000015"  # the 18th's price, not the 17th's


def test_naive_clock_is_refused_before_writing_anything(settings: Settings) -> None:
    client = TestClient(
        create_app(settings, clock=lambda: datetime(2026, 11, 17, 12, 0)),
        raise_server_exceptions=False,
    )
    tenant, admin = bootstrap(client)
    r = client.post("/v1/chat", headers=auth(create_key(client, admin, "developer")), json=HI)
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert audit_rows(client, tenant) == []
    # The tenant's read endpoints keep working.
    assert client.get("/v1/audit", headers=auth(admin), params=WINDOW).status_code == 200


def test_audit_record_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AuditRecord(
            id="r",
            tenant_id="t",
            key_id="k",
            key_prefix="p",
            provider="fake",
            model="fake-echo",
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal(0),
            status="error",
            error_code="x",
            latency_ms=0,
            created_at=datetime(2026, 11, 17, 12, 0),
        )


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_viewers_and_admins_can_read(client: TestClient, role: str) -> None:
    admin, _, viewer = setup_calls(client)
    key = viewer if role == "viewer" else admin
    assert client.get("/v1/audit", headers=auth(key), params=WINDOW).status_code == 200
    assert client.get("/v1/usage", headers=auth(key), params=WINDOW).status_code == 200


@pytest.mark.parametrize("path", ["/v1/audit", "/v1/usage"])
def test_developer_cannot_read(client: TestClient, path: str) -> None:
    _, dev, _ = setup_calls(client)
    assert client.get(path, headers=auth(dev), params=WINDOW).status_code == 403


@pytest.mark.parametrize("path", ["/v1/audit", "/v1/usage"])
@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-18T00:00:00Z", "end": "2026-11-17T00:00:00Z"},
        {"start": "2026-11-17T00:00:00Z", "end": "2026-11-17T00:00:00Z"},
    ],
)
def test_bad_range_is_422(client: TestClient, path: str, params: dict[str, str]) -> None:
    _, _, viewer = setup_calls(client)
    r = client.get(path, headers=auth(viewer), params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_range"


@pytest.mark.parametrize("path", ["/v1/audit", "/v1/usage"])
def test_window_longer_than_the_limit_is_422(client: TestClient, path: str) -> None:
    _, _, viewer = setup_calls(client)
    too_long = {"start": "2026-01-01T00:00:00Z", "end": "2027-01-02T00:00:01Z"}  # > 366 days
    r = client.get(path, headers=auth(viewer), params=too_long)
    assert r.status_code == 422 and r.json()["error"]["code"] == "range_too_large"
    exactly = {"start": "2026-01-01T00:00:00Z", "end": "2027-01-02T00:00:00Z"}  # 366 days
    assert client.get(path, headers=auth(viewer), params=exactly).status_code == 200


@pytest.mark.parametrize(
    "params", [{}, {"start": "2026-11-17T00:00:00", "end": "2026-11-18T00:00:00Z"}]
)
def test_missing_or_naive_dates_are_422(client: TestClient, params: dict[str, str]) -> None:
    _, _, viewer = setup_calls(client)
    r = client.get("/v1/usage", headers=auth(viewer), params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
