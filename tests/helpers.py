"""Shared test helpers. Import with ``from tests.helpers import ...``."""

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from llm_gateway.audit.records import AuditRecord, position_of
from llm_gateway.providers.base import ChatRequest, ChatResponse
from llm_gateway.providers.fake import FakeProvider

ADMIN_TOKEN = "test-admin-token"  # matches the `settings` fixture
HI = {"messages": [{"role": "user", "content": "hi"}]}


class SpyProvider:
    """Counts calls and delegates to ``FakeProvider``."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self._inner = FakeProvider()

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        self.calls += 1
        return await self._inner.chat(request, model)


class RaisingProvider:
    """Raises ``exc`` on every call (also used with ``asyncio.CancelledError()``)."""

    name = "fake"

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        raise self.exc


def audit_rows(client: TestClient, tenant_id: str) -> list[AuditRecord]:
    """Every audit row of a tenant. Sync tests only, and only with in-memory repos."""
    repo = client.app.state.gateway.audit  # type: ignore[attr-defined]
    start = datetime(2000, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)

    async def every_page() -> list[AuditRecord]:
        rows: list[AuditRecord] = []
        after = None
        while page := await repo.list_page(tenant_id, start, end, limit=500, after=after):
            rows += page
            after = position_of(page[-1])
        return rows

    return asyncio.run(every_page())


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def bootstrap(client: TestClient, name: str = "acme") -> tuple[str, str]:
    """Create a tenant; return ``(tenant_id, admin_key)``."""
    r = client.post(
        "/v1/admin/tenants", json={"name": name}, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["tenant_id"], body["admin_key"]


def create_key(client: TestClient, admin_key: str, role: str) -> str:
    """Create a key with ``role`` in the admin's tenant; return the full key."""
    r = client.post("/v1/admin/keys", headers=auth(admin_key), json={"role": role})
    assert r.status_code == 201, r.text
    key: str = r.json()["key"]
    return key


def find_key_id(client: TestClient, admin_key: str, key: str) -> str:
    """Look up a key's id by its prefix through the admin list endpoint."""
    prefix = key.split("_")[1]
    r = client.get("/v1/admin/keys", headers=auth(admin_key))
    assert r.status_code == 200, r.text
    [match] = [k for k in r.json() if k["prefix"] == prefix]
    key_id: str = match["key_id"]
    return key_id
