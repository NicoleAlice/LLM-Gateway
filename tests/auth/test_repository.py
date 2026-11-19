import dataclasses
from datetime import datetime, timedelta

import pytest

from llm_gateway.auth.keys import Role, Tenant, generate_key
from llm_gateway.auth.repository import (
    DuplicateKeyPrefixError,
    InMemoryKeyRepository,
    RevokeOutcome,
)
from tests.contracts import Repos


async def test_revoke_is_scoped_and_idempotent(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    _, key = generate_key("t1", Role.DEVELOPER, fixed_now)
    await repo.add(key)
    assert await repo.revoke("t2", key.id, fixed_now) == (RevokeOutcome.NOT_FOUND, None)
    stored = await repo.get_by_prefix(key.prefix)
    assert stored is not None and stored.is_active
    outcome, revoked = await repo.revoke("t1", key.id, fixed_now)
    assert outcome is RevokeOutcome.REVOKED
    assert revoked is not None and revoked.revoked_at == fixed_now
    later = fixed_now + timedelta(hours=1)
    outcome, again = await repo.revoke("t1", key.id, later)
    assert outcome is RevokeOutcome.ALREADY_REVOKED
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
    _, revoked = await repo.revoke("t1", first.id, fixed_now, keep_one_admin=False)
    assert revoked is not None and revoked.prefix == first.prefix


async def test_equal_timestamps_are_ordered_by_id(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    keys = [generate_key("t1", Role.VIEWER, fixed_now)[1] for _ in range(5)]
    for key in keys:
        await repo.add(key)
    assert [k.id for k in await repo.list_for_tenant("t1")] == sorted(k.id for k in keys)


async def test_duplicate_tenant_is_rejected(repos: Repos) -> None:
    repo = repos.tenants
    await repo.add(Tenant(id="t1", name="acme"))
    with pytest.raises(ValueError, match="duplicate tenant id") as excinfo:
        await repo.add(Tenant(id="t1", name="other"))
    # Raised outside the driver's except: no database error (or its SQL) travels with it.
    assert excinfo.value.__context__ is None and excinfo.value.__cause__ is None
    assert await repo.get("t1") == Tenant(id="t1", name="acme")


@pytest.mark.parametrize("name", ["Ação & Cia — 東京 🚀", "x" * 100, "a'; DROP TABLE tenants; --"])
async def test_tenant_names_round_trip_unchanged(repos: Repos, name: str) -> None:
    await repos.tenants.add(Tenant(id="t1", name=name))
    assert await repos.tenants.get("t1") == Tenant(id="t1", name=name)


async def test_revoke_unknown_key_is_not_found(fixed_now: datetime) -> None:
    result = await InMemoryKeyRepository().revoke("t1", "no-such-id", fixed_now)
    assert result == (RevokeOutcome.NOT_FOUND, None)


async def admins(repo: InMemoryKeyRepository, now: datetime, count: int) -> list[str]:
    ids = []
    for _ in range(count):
        _, key = generate_key("t1", Role.ADMIN, now)
        await repo.add(key)
        ids.append(key.id)
    return ids


async def test_the_only_active_admin_key_cannot_be_revoked(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    [only] = await admins(repo, fixed_now, 1)
    outcome, key = await repo.revoke("t1", only, fixed_now)
    assert outcome is RevokeOutcome.LAST_ADMIN
    assert key is not None and key.is_active
    [listed] = await repo.list_for_tenant("t1")
    assert listed.is_active  # nothing was written


async def test_with_two_admins_one_can_go_then_the_other_cannot(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    first, second = await admins(repo, fixed_now, 2)
    assert (await repo.revoke("t1", first, fixed_now))[0] is RevokeOutcome.REVOKED
    assert (await repo.revoke("t1", second, fixed_now))[0] is RevokeOutcome.LAST_ADMIN


async def test_revoked_admins_do_not_count(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    first, second, third = await admins(repo, fixed_now, 3)
    await repo.revoke("t1", first, fixed_now)
    await repo.revoke("t1", second, fixed_now)
    assert (await repo.revoke("t1", third, fixed_now))[0] is RevokeOutcome.LAST_ADMIN


async def test_admins_of_other_tenants_do_not_count(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    [mine] = await admins(repo, fixed_now, 1)
    _, foreign = generate_key("t2", Role.ADMIN, fixed_now)
    await repo.add(foreign)
    assert (await repo.revoke("t1", mine, fixed_now))[0] is RevokeOutcome.LAST_ADMIN


async def test_non_admin_keys_are_not_held_back(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    await admins(repo, fixed_now, 1)
    _, viewer = generate_key("t1", Role.VIEWER, fixed_now)
    await repo.add(viewer)
    assert (await repo.revoke("t1", viewer.id, fixed_now))[0] is RevokeOutcome.REVOKED


async def test_already_revoked_admin_is_reported_as_such(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    first, _ = await admins(repo, fixed_now, 2)
    await repo.revoke("t1", first, fixed_now)
    assert (await repo.revoke("t1", first, fixed_now))[0] is RevokeOutcome.ALREADY_REVOKED


async def test_rule_can_be_turned_off(fixed_now: datetime) -> None:
    repo = InMemoryKeyRepository()
    [only] = await admins(repo, fixed_now, 1)
    outcome, key = await repo.revoke("t1", only, fixed_now, keep_one_admin=False)
    assert outcome is RevokeOutcome.REVOKED and key is not None and not key.is_active


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


async def test_tenant_repository_roundtrip(repos: Repos) -> None:
    repo = repos.tenants
    await repo.add(Tenant(id="t1", name="acme"))
    assert await repo.get("t1") == Tenant(id="t1", name="acme")
    assert await repo.get("t2") is None
