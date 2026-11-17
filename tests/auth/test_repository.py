import dataclasses
from datetime import datetime, timedelta

import pytest

from llm_gateway.auth.keys import Role, Tenant, generate_key
from llm_gateway.auth.repository import (
    DuplicateKeyPrefixError,
    InMemoryKeyRepository,
    InMemoryTenantRepository,
)


async def test_revoke_is_scoped_and_idempotent(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    _, key = generate_key("t1", Role.DEVELOPER, fixed_now)
    await repo.add(key)
    assert await repo.revoke("t2", key.id, fixed_now) is None
    stored = await repo.get_by_prefix(key.prefix)
    assert stored is not None and stored.is_active
    revoked = await repo.revoke("t1", key.id, fixed_now)
    assert revoked is not None and revoked.revoked_at == fixed_now
    later = fixed_now + timedelta(hours=1)
    again = await repo.revoke("t1", key.id, later)
    assert again is not None and again.revoked_at == fixed_now


async def test_duplicate_prefix_is_rejected(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    _, first = generate_key("t1", Role.ADMIN, fixed_now)
    await repo.add(first)
    with pytest.raises(DuplicateKeyPrefixError):
        await repo.add(dataclasses.replace(first, id="other", tenant_id="t2"))
    stored = await repo.get_by_prefix(first.prefix)
    assert stored is not None and stored.id == first.id


async def test_duplicate_id_is_rejected(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    _, first = generate_key("t1", Role.ADMIN, fixed_now)
    _, second = generate_key("t1", Role.ADMIN, fixed_now)
    await repo.add(first)
    with pytest.raises(ValueError, match="duplicate key id"):
        await repo.add(dataclasses.replace(second, id=first.id))
    revoked = await repo.revoke("t1", first.id, fixed_now)
    assert revoked is not None and revoked.prefix == first.prefix


async def test_equal_timestamps_are_ordered_by_id(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    keys = [generate_key("t1", Role.VIEWER, fixed_now)[1] for _ in range(5)]
    for key in keys:
        await repo.add(key)
    assert [k.id for k in await repo.list_for_tenant("t1")] == sorted(k.id for k in keys)


async def test_duplicate_tenant_is_rejected() -> None:
    repo = InMemoryTenantRepository()
    await repo.add(Tenant(id="t1", name="acme"))
    with pytest.raises(ValueError, match="duplicate tenant id"):
        await repo.add(Tenant(id="t1", name="other"))
    assert await repo.get("t1") == Tenant(id="t1", name="acme")


async def test_revoke_unknown_key_returns_none(fixed_now: datetime) -> None:
    assert await InMemoryKeyRepository().revoke("t1", "no-such-id", fixed_now) is None


async def test_list_is_per_tenant_and_oldest_first(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    later = fixed_now + timedelta(minutes=5)
    _, newer = generate_key("t1", Role.VIEWER, later)
    _, older = generate_key("t1", Role.ADMIN, fixed_now)
    _, other = generate_key("t2", Role.ADMIN, fixed_now)
    for key in (newer, older, other):
        await repo.add(key)
    assert [k.id for k in await repo.list_for_tenant("t1")] == [older.id, newer.id]
    assert [k.id for k in await repo.list_for_tenant("t2")] == [other.id]
    assert await repo.list_for_tenant("t3") == []


async def test_list_includes_revoked_keys(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    _, key = generate_key("t1", Role.VIEWER, fixed_now)
    await repo.add(key)
    await repo.revoke("t1", key.id, fixed_now)
    [listed] = await repo.list_for_tenant("t1")
    assert listed.revoked_at == fixed_now


async def test_tenant_repository_roundtrip() -> None:
    repo = InMemoryTenantRepository()
    await repo.add(Tenant(id="t1", name="acme"))
    assert await repo.get("t1") == Tenant(id="t1", name="acme")
    assert await repo.get("t2") is None
