from collections.abc import Iterator

from fastapi.testclient import TestClient

from llm_gateway.config import Settings
from llm_gateway.main import create_app
from tests.helpers import ADMIN_TOKEN, auth, bootstrap

LIMIT = 2048


def small_limit_client() -> TestClient:
    settings = Settings(_env_file=None, admin_token=ADMIN_TOKEN, max_body_bytes=LIMIT)
    return TestClient(create_app(settings))


def test_oversized_body_is_413_before_auth() -> None:
    client = small_limit_client()
    r = client.post("/v1/admin/keys", content=b"x" * (LIMIT + 1))  # no Authorization
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_declared_oversize_is_refused_without_reading_the_body() -> None:
    # The body sent is tiny; only the declared length is too big. Reading it would
    # reach auth (401), so a 413 proves the fast path refused before reading.
    client = small_limit_client()
    r = client.post("/v1/admin/keys", content=b"{}", headers={"Content-Length": str(LIMIT + 1)})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_oversized_chunked_body_is_413() -> None:
    client = small_limit_client()
    _, admin = bootstrap(client)

    def chunks() -> Iterator[bytes]:  # no Content-Length: sent chunked
        for _ in range(4):
            yield b'{"role": "' + b"v" * 1000

    headers = {**auth(admin), "Content-Type": "application/json"}
    r = client.post("/v1/admin/keys", content=chunks(), headers=headers)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    # Same message as the Content-Length fast path.
    fast = client.post("/v1/admin/keys", content=b"x" * (LIMIT + 1))
    assert r.json()["error"]["message"] == fast.json()["error"]["message"]


def test_body_at_the_limit_is_accepted() -> None:
    client = small_limit_client()
    _, admin = bootstrap(client)
    body = b'{"role": "viewer"}'
    padded = body[:-1] + b" " * (LIMIT - len(body)) + b"}"
    assert len(padded) == LIMIT
    headers = {**auth(admin), "Content-Type": "application/json"}
    assert client.post("/v1/admin/keys", content=padded, headers=headers).status_code == 201


def test_default_limit_is_one_mebibyte() -> None:
    assert Settings(_env_file=None, admin_token=ADMIN_TOKEN).max_body_bytes == 1_048_576


def test_malformed_json_is_422_even_without_a_key(client: TestClient) -> None:
    # Documented behaviour: FastAPI decodes JSON before running auth dependencies.
    r = client.post(
        "/v1/admin/keys", content=b'{"role": ', headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
