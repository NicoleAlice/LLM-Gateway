import base64
from datetime import timedelta

from fastapi.testclient import TestClient

from llm_gateway.audit.cursor import MAX_CURSOR_LENGTH
from tests.helpers import HI, auth, bootstrap, create_key

WINDOW = {"start": "2026-11-17T00:00:00Z", "end": "2026-11-18T00:00:00Z"}


def tenant_with_calls(client: TestClient, calls: int, name: str = "acme") -> tuple[str, str]:
    _, admin = bootstrap(client, name)
    dev = create_key(client, admin, "developer")
    for _ in range(calls):
        assert client.post("/v1/chat", headers=auth(dev), json=HI).status_code == 200
    return admin, dev


def page(client: TestClient, key: str, **params: object) -> dict[str, object]:
    r = client.get("/v1/audit", headers=auth(key), params={**WINDOW, **params})
    assert r.status_code == 200, r.text
    body: dict[str, object] = r.json()
    return body


def ids(body: dict[str, object]) -> list[str]:
    items = body["items"]
    assert isinstance(items, list)
    return [item["id"] for item in items]


def test_pages_walk_the_whole_window(client: TestClient) -> None:
    # Every call shares the fixed clock's timestamp, so pages must split ties by id.
    admin, _ = tenant_with_calls(client, 7)
    seen: list[str] = []
    sizes = []
    cursor = None
    while True:
        body = page(client, admin, limit=3, **({"cursor": cursor} if cursor else {}))
        sizes.append(len(ids(body)))
        seen += ids(body)
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sizes == [3, 3, 1]
    assert len(seen) == 7 and len(set(seen)) == 7
    assert seen == ids(page(client, admin, limit=10))  # same order as one big page


def test_exact_multiple_of_the_limit_has_no_empty_extra_page(client: TestClient) -> None:
    admin, _ = tenant_with_calls(client, 4)
    first = page(client, admin, limit=2)
    second = page(client, admin, limit=2, cursor=first["next_cursor"])
    assert len(ids(second)) == 2 and second["next_cursor"] is None


def test_default_limit_is_100(client: TestClient) -> None:
    admin, _ = tenant_with_calls(client, 101)
    body = page(client, admin)
    assert len(ids(body)) == 100 and body["next_cursor"] is not None


def test_limit_bounds(client: TestClient) -> None:
    admin, _ = tenant_with_calls(client, 1)
    for bad in (0, 1001, -1):
        r = client.get("/v1/audit", headers=auth(admin), params={**WINDOW, "limit": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert len(ids(page(client, admin, limit=1000))) == 1


def test_every_bad_cursor_is_422_invalid_cursor(client: TestClient) -> None:
    admin, _ = tenant_with_calls(client, 1)
    year_one = '{"t":"0001-01-01T00:00:00+05:00","i":"' + "0" * 32 + '"}'  # overflows in UTC
    edge = base64.urlsafe_b64encode(year_one.encode()).decode().rstrip("=")
    for cursor in ("garbage!", "a" * MAX_CURSOR_LENGTH, "a" * (MAX_CURSOR_LENGTH + 1), edge):
        r = client.get("/v1/audit", headers=auth(admin), params={**WINDOW, "cursor": cursor})
        assert r.status_code == 422, cursor
        assert r.json()["error"]["code"] == "invalid_cursor"
        assert cursor not in r.text


def test_cursor_from_another_tenant_only_moves_within_their_rows(client: TestClient) -> None:
    admin_a, _ = tenant_with_calls(client, 3, "a")
    admin_b, _ = tenant_with_calls(client, 2, "b")
    cursor_a = page(client, admin_a, limit=1)["next_cursor"]
    replayed = ids(page(client, admin_b, cursor=cursor_a))
    assert not set(replayed) & set(ids(page(client, admin_a)))  # nothing of A's
    assert set(replayed) <= set(ids(page(client, admin_b)))  # only B's own rows


def test_cursor_cannot_widen_the_window(client: TestClient) -> None:
    admin, dev = tenant_with_calls(client, 2)  # both at 12:00:00
    cursor = page(client, admin, limit=1)["next_cursor"]
    clock = client.app.state.gateway.clock  # type: ignore[attr-defined]
    clock.set(clock() + timedelta(minutes=30))
    client.post("/v1/chat", headers=auth(dev), json=HI)  # one row at 12:30:00
    later = {"start": "2026-11-17T12:10:00Z", "end": "2026-11-18T00:00:00Z"}
    r = client.get("/v1/audit", headers=auth(admin), params={**later, "cursor": cursor})
    assert r.status_code == 200
    [only] = r.json()["items"]  # the 12:00 row after the cursor is outside the window
    assert only["created_at"] == "2026-11-17T12:30:00Z"


def test_developer_still_cannot_read(client: TestClient) -> None:
    _, dev = tenant_with_calls(client, 1)
    assert client.get("/v1/audit", headers=auth(dev), params=WINDOW).status_code == 403
