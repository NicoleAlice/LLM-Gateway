import pytest
from fastapi.testclient import TestClient

import llm_gateway.auth.keys as keys_module
from llm_gateway.auth.keys import Tenant
from tests.helpers import ADMIN_TOKEN, auth, bootstrap, create_key, find_key_id


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Admin-Token": ""},
        {"X-Admin-Token": "wrong"},
        {"X-Admin-Token": "test-admin"},
        {"X-Admin-Token": bytes([233])},
    ],
)
def test_bootstrap_requires_exact_admin_token(
    client: TestClient, headers: dict[str, str | bytes]
) -> None:
    r = client.post("/v1/admin/tenants", json={"name": "acme"}, headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_admin_token"


def test_bad_authorization_is_401(client: TestClient) -> None:
    _, admin = bootstrap(client)
    for headers in (
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": admin},
        {"Authorization": f"Basic {admin}"},
    ):
        r = client.get("/v1/admin/keys", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    ok = client.get("/v1/admin/keys", headers={"Authorization": f"bearer {admin}"})
    assert ok.status_code == 200


def test_right_prefix_wrong_secret_is_401(client: TestClient) -> None:
    _, admin = bootstrap(client)
    prefix = admin.split("_")[1]
    forged = f"llmgw_{prefix}_{'A' * 43}"
    r = client.get("/v1/admin/keys", headers=auth(forged))
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"


def test_401_tells_client_to_use_bearer(client: TestClient) -> None:
    r = client.get("/v1/admin/keys")
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("role", ["admin", "developer", "viewer"])
def test_created_key_gets_requested_role(client: TestClient, role: str) -> None:
    _, admin = bootstrap(client)
    r = client.post("/v1/admin/keys", headers=auth(admin), json={"role": role})
    assert r.status_code == 201 and r.json()["role"] == role
    listed = {
        k["key_id"]: k["role"] for k in client.get("/v1/admin/keys", headers=auth(admin)).json()
    }
    assert listed[r.json()["key_id"]] == role


def test_key_list_is_per_tenant(client: TestClient) -> None:
    _, admin_a = bootstrap(client, "a")
    _, admin_b = bootstrap(client, "b")
    create_key(client, admin_b, "viewer")
    listed = client.get("/v1/admin/keys", headers=auth(admin_a)).json()
    assert [k["prefix"] for k in listed] == [admin_a.split("_")[1]]


def test_revoking_twice_is_idempotent(client: TestClient) -> None:
    _, admin = bootstrap(client)
    viewer = create_key(client, admin, "viewer")
    key_id = find_key_id(client, admin, viewer)
    assert client.delete(f"/v1/admin/keys/{key_id}", headers=auth(admin)).status_code == 204
    first = next(
        k for k in client.get("/v1/admin/keys", headers=auth(admin)).json() if k["key_id"] == key_id
    )
    assert client.delete(f"/v1/admin/keys/{key_id}", headers=auth(admin)).status_code == 204
    again = next(
        k for k in client.get("/v1/admin/keys", headers=auth(admin)).json() if k["key_id"] == key_id
    )
    assert first["revoked_at"] is not None and again["revoked_at"] == first["revoked_at"]


def test_failed_bootstrap_leaves_no_tenant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, admin = bootstrap(client)
    taken = admin.split("_")[1]
    real = keys_module.secrets.token_hex
    monkeypatch.setattr(keys_module.secrets, "token_hex", lambda n: taken if n == 6 else real(n))
    tenants = client.app.state.gateway.tenants  # type: ignore[attr-defined]
    before = len(tenants._tenants)
    r = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/admin/tenants", json={"name": "orphan"}, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert r.status_code == 500
    assert len(tenants._tenants) == before


def test_bootstrap_stores_the_tenant(client: TestClient) -> None:
    tenant_id, _ = bootstrap(client, "acme")
    tenants = client.app.state.gateway.tenants  # type: ignore[attr-defined]
    stored = client.portal.call(tenants.get, tenant_id)  # type: ignore[union-attr]
    assert stored == Tenant(id=tenant_id, name="acme")


@pytest.mark.parametrize(
    ("path", "body", "use_admin_token"),
    [
        ("/v1/admin/tenants", {"name": "acme", "plan": "free"}, True),
        ("/v1/admin/keys", {"role": "viewer", "scopes": ["*"]}, False),
    ],
)
def test_unknown_fields_are_rejected(
    client: TestClient, path: str, body: dict[str, object], use_admin_token: bool
) -> None:
    headers = {"X-Admin-Token": ADMIN_TOKEN} if use_admin_token else auth(bootstrap(client)[1])
    r = client.post(path, json=body, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("role", ["developer", "viewer"])
def test_non_admin_cannot_manage_keys(client: TestClient, role: str) -> None:
    _, admin = bootstrap(client)
    key = create_key(client, admin, role)
    assert client.get("/v1/admin/keys", headers=auth(key)).status_code == 403
    r = client.post("/v1/admin/keys", headers=auth(key), json={"role": "viewer"})
    assert r.status_code == 403


def test_created_key_has_requested_role_and_list_hides_secrets(client: TestClient) -> None:
    _, admin = bootstrap(client)
    viewer = create_key(client, admin, "viewer")
    r = client.get("/v1/admin/keys", headers=auth(admin))
    body = r.json()
    assert len(body) == 2
    assert {k["role"] for k in body} == {"admin", "viewer"}
    assert all(set(k) == {"key_id", "prefix", "role", "created_at", "revoked_at"} for k in body)
    for raw in (admin, viewer):
        assert raw.split("_", 2)[2] not in r.text


def test_revocation_is_instant(client: TestClient) -> None:
    _, admin = bootstrap(client)
    viewer = create_key(client, admin, "viewer")
    assert client.get("/v1/admin/keys", headers=auth(viewer)).status_code == 403  # authenticated
    r = client.delete(f"/v1/admin/keys/{find_key_id(client, admin, viewer)}", headers=auth(admin))
    assert r.status_code == 204
    assert client.get("/v1/admin/keys", headers=auth(viewer)).status_code == 401  # now rejected


@pytest.mark.parametrize("name", ["", "x" * 101])
def test_tenant_name_bounds(client: TestClient, name: str) -> None:
    r = client.post(
        "/v1/admin/tenants", json={"name": name}, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_revoked_admins_do_not_count_as_remaining(client: TestClient) -> None:
    _, admin = bootstrap(client)
    second = create_key(client, admin, "admin")
    r = client.delete(f"/v1/admin/keys/{find_key_id(client, admin, second)}", headers=auth(admin))
    assert r.status_code == 204
    r = client.delete(f"/v1/admin/keys/{find_key_id(client, admin, admin)}", headers=auth(admin))
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin_key"


def test_key_creation_retries_on_prefix_collision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, admin = bootstrap(client)
    taken = admin.split("_")[1]
    real = keys_module.secrets.token_hex
    seq = iter([taken, taken, "abcdefabcdef"])
    monkeypatch.setattr(
        keys_module.secrets, "token_hex", lambda n: next(seq) if n == 6 else real(n)
    )
    r = client.post("/v1/admin/keys", headers=auth(admin), json={"role": "viewer"})
    assert r.status_code == 201 and r.json()["prefix"] == "abcdefabcdef"
    attempts: list[int] = []

    def always_taken(n: int) -> str:
        if n != 6:
            return real(n)
        attempts.append(n)
        return taken

    monkeypatch.setattr(keys_module.secrets, "token_hex", always_taken)
    r = TestClient(client.app, raise_server_exceptions=False).post(
        "/v1/admin/keys", headers=auth(admin), json={"role": "viewer"}
    )
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert r.json()["error"]["message"] == "internal server error"  # no exception details
    assert "prefix" not in r.text
    assert len(attempts) == 3


def test_cannot_revoke_other_tenants_key(client: TestClient) -> None:
    _, admin_a = bootstrap(client, "a")
    _, admin_b = bootstrap(client, "b")
    victim = find_key_id(client, admin_b, admin_b)
    r = client.delete(f"/v1/admin/keys/{victim}", headers=auth(admin_a))
    assert r.status_code == 404 and r.json()["error"]["code"] == "key_not_found"
    assert client.get("/v1/admin/keys", headers=auth(admin_b)).status_code == 200


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("REVOKED", 204, None),
        ("ALREADY_REVOKED", 204, None),
        ("NOT_FOUND", 404, "key_not_found"),
        ("LAST_ADMIN", 409, "last_admin_key"),
    ],
)
def test_route_only_maps_the_repository_outcome(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    status: int,
    code: str | None,
) -> None:
    from llm_gateway.auth.repository import RevokeOutcome

    _, admin = bootstrap(client)
    keys = client.app.state.gateway.keys  # type: ignore[attr-defined]
    calls: list[tuple[object, ...]] = []

    async def fake_revoke(tenant_id: str, key_id: str, now: object, **kw: object) -> object:
        calls.append((tenant_id, key_id, kw))
        return RevokeOutcome[outcome], None

    async def no_listing(tenant_id: str) -> object:
        raise AssertionError("the route must not decide on its own")

    monkeypatch.setattr(keys, "revoke", fake_revoke)
    monkeypatch.setattr(keys, "list_for_tenant", no_listing)
    r = client.delete("/v1/admin/keys/some-key", headers=auth(admin))
    assert r.status_code == status
    if code is not None:
        assert r.json()["error"]["code"] == code
    [(_, key_id, kw)] = calls
    assert key_id == "some-key" and kw == {"keep_one_admin": True}


def test_cannot_revoke_last_admin_key(client: TestClient) -> None:
    _, admin = bootstrap(client)
    r = client.delete(f"/v1/admin/keys/{find_key_id(client, admin, admin)}", headers=auth(admin))
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin_key"
