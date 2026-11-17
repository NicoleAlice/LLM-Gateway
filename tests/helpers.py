"""Shared test helpers. Import with ``from tests.helpers import ...``."""

from fastapi.testclient import TestClient

ADMIN_TOKEN = "test-admin-token"  # matches the `settings` fixture


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
