import pytest
from fastapi.testclient import TestClient

from tests.helpers import auth, bootstrap, create_key


def test_put_rejects_unknown_model(client: TestClient) -> None:
    _, admin = bootstrap(client)
    r = client.put(
        "/v1/admin/routing", headers=auth(admin), json={"mode": "mandatory", "model": "nope"}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"
    assert client.get("/v1/admin/routing", headers=auth(admin)).status_code == 404


def test_rule_roundtrip(client: TestClient) -> None:
    _, admin = bootstrap(client)
    r = client.get("/v1/admin/routing", headers=auth(admin))
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_routing_rule"
    body = {"mode": "preferred", "model": "fake-echo"}
    put = client.put("/v1/admin/routing", headers=auth(admin), json=body)
    assert put.status_code == 200 and put.json() == body
    assert client.get("/v1/admin/routing", headers=auth(admin)).json() == body


def test_delete_clears_and_is_idempotent(client: TestClient) -> None:
    _, admin = bootstrap(client)
    client.put(
        "/v1/admin/routing", headers=auth(admin), json={"mode": "preferred", "model": "fake-echo"}
    )
    assert client.delete("/v1/admin/routing", headers=auth(admin)).status_code == 204
    assert client.get("/v1/admin/routing", headers=auth(admin)).status_code == 404
    assert client.delete("/v1/admin/routing", headers=auth(admin)).status_code == 204


def test_rules_are_per_tenant(client: TestClient) -> None:
    _, admin_a = bootstrap(client, "a")
    _, admin_b = bootstrap(client, "b")
    body = {"mode": "mandatory", "model": "fake-echo"}
    client.put("/v1/admin/routing", headers=auth(admin_a), json=body)
    assert client.get("/v1/admin/routing", headers=auth(admin_b)).status_code == 404
    client.delete("/v1/admin/routing", headers=auth(admin_b))
    assert client.get("/v1/admin/routing", headers=auth(admin_a)).json() == body
    other = {"mode": "preferred", "model": "fake-echo"}
    client.put("/v1/admin/routing", headers=auth(admin_b), json=other)
    assert client.get("/v1/admin/routing", headers=auth(admin_a)).json() == body
    assert client.get("/v1/admin/routing", headers=auth(admin_b)).json() == other


@pytest.mark.parametrize("role", ["developer", "viewer"])
def test_only_admins_manage_routing(client: TestClient, role: str) -> None:
    _, admin = bootstrap(client)
    key = create_key(client, admin, role)
    body = {"mode": "preferred", "model": "fake-echo"}
    assert client.get("/v1/admin/routing", headers=auth(key)).status_code == 403
    assert client.put("/v1/admin/routing", headers=auth(key), json=body).status_code == 403
    assert client.delete("/v1/admin/routing", headers=auth(key)).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "sometimes", "model": "fake-echo"},
        {"mode": "preferred"},
        {"mode": "preferred", "model": "fake-echo", "weight": 1},
        {"mode": "preferred", "model": ""},
    ],
)
def test_invalid_rule_body_is_422(client: TestClient, body: dict[str, object]) -> None:
    _, admin = bootstrap(client)
    r = client.put("/v1/admin/routing", headers=auth(admin), json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
