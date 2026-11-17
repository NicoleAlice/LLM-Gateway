from fastapi.testclient import TestClient

from tests.helpers import auth, bootstrap


def test_unknown_route_uses_error_contract(client: TestClient) -> None:
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_wrong_method_uses_error_contract(client: TestClient) -> None:
    r = client.post("/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in r.headers["allow"]


def test_unknown_field_names_are_not_echoed(client: TestClient) -> None:
    _, admin = bootstrap(client)
    body = {"role": "viewer", "sk-live-SECRET123": 1}
    r = client.post("/v1/admin/keys", headers=auth(admin), json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert "SECRET123" not in r.text
    assert "unknown field" in r.json()["error"]["message"]


def test_nonstandard_http_status_keeps_error_shape(client: TestClient) -> None:
    from fastapi import HTTPException

    app = client.app

    @app.get("/teapot-ish")  # type: ignore[attr-defined, untyped-decorator]
    async def odd() -> None:
        raise HTTPException(status_code=599)

    r = client.get("/teapot-ish")
    assert r.status_code == 599 and r.json()["error"]["code"] == "http_error"


def test_validation_error_uses_contract_and_hides_input(client: TestClient) -> None:
    _, admin = bootstrap(client)
    r = client.post("/v1/admin/keys", headers=auth(admin), json={"role": "root-secret-xyz"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert "root-secret-xyz" not in r.text
