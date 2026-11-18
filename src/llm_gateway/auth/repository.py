"""Storage for tenants and API keys.

Repositories are async Protocols so a Postgres implementation can replace the in-memory
one without changing any route.
"""

import dataclasses
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from llm_gateway.auth.keys import ApiKey, Role, Tenant


class DuplicateKeyPrefixError(Exception):
    """Another key already uses this prefix. Callers regenerate the key and retry."""


class RevokeOutcome(StrEnum):
    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"  # the first revoked_at is kept
    NOT_FOUND = "not_found"  # no such key in this tenant (other tenants' keys included)
    LAST_ADMIN = "last_admin"  # refused: it is the tenant's only active admin key


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None:
        """Store a tenant. Raises ``ValueError`` if the id is taken."""
        ...

    async def get(self, tenant_id: str) -> Tenant | None: ...


class KeyRepository(Protocol):
    async def add(self, key: ApiKey) -> None:
        """Store a key. Raises ``DuplicateKeyPrefixError`` if the prefix is taken
        (callers retry with a new key) and ``ValueError`` if the id is taken (a bug)."""
        ...

    async def get_by_prefix(self, prefix: str) -> ApiKey | None: ...

    async def list_for_tenant(self, tenant_id: str) -> list[ApiKey]:
        """All of a tenant's keys, revoked ones included, ordered by ``(created_at, id)``."""
        ...

    async def revoke(
        self, tenant_id: str, key_id: str, now: datetime, *, keep_one_admin: bool = True
    ) -> tuple[RevokeOutcome, ApiKey | None]:
        """Revoke a key of *this* tenant, as **one atomic operation**.

        With ``keep_one_admin``, an active admin key is refused (``LAST_ADMIN``) when no
        *other* active admin key exists, so a tenant can't lock itself out. The check
        and the write must not interleave with another revoke: PostgreSQL does both in
        one transaction, locking the tenant's active admin keys (``SELECT ... FOR UPDATE``).
        Returns the outcome and the key as it is afterwards (``None`` if not found)."""
        ...


class InMemoryTenantRepository:
    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}

    async def add(self, tenant: Tenant) -> None:
        if tenant.id in self._tenants:
            raise ValueError(f"duplicate tenant id {tenant.id}")
        self._tenants[tenant.id] = tenant

    async def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)


class InMemoryKeyRepository:
    def __init__(self) -> None:
        self._by_prefix: dict[str, ApiKey] = {}
        self._prefix_by_id: dict[str, str] = {}

    async def add(self, key: ApiKey) -> None:
        if key.prefix in self._by_prefix:
            raise DuplicateKeyPrefixError(key.prefix)
        if key.id in self._prefix_by_id:
            # Would leave the older key active but unreachable by revoke(id).
            raise ValueError(f"duplicate key id {key.id}")
        self._by_prefix[key.prefix] = key
        self._prefix_by_id[key.id] = key.prefix

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        return self._by_prefix.get(prefix)

    async def list_for_tenant(self, tenant_id: str) -> list[ApiKey]:
        keys = [k for k in self._by_prefix.values() if k.tenant_id == tenant_id]
        # id breaks ties so the order is deterministic, as `ORDER BY created_at, id` will be.
        return sorted(keys, key=lambda k: (k.created_at, k.id))

    async def revoke(
        self, tenant_id: str, key_id: str, now: datetime, *, keep_one_admin: bool = True
    ) -> tuple[RevokeOutcome, ApiKey | None]:
        # Atomic in memory: no await between the check and the write, so no other
        # coroutine can run in between.
        prefix = self._prefix_by_id.get(key_id)
        key = self._by_prefix.get(prefix) if prefix is not None else None
        if key is None or key.tenant_id != tenant_id:
            return RevokeOutcome.NOT_FOUND, None
        if key.revoked_at is not None:
            return RevokeOutcome.ALREADY_REVOKED, key
        if keep_one_admin and key.role is Role.ADMIN:
            others = [
                k
                for k in self._by_prefix.values()
                if k.tenant_id == tenant_id
                and k.role is Role.ADMIN
                and k.is_active
                and k.id != key.id
            ]
            if not others:
                return RevokeOutcome.LAST_ADMIN, key
        revoked = dataclasses.replace(key, revoked_at=now)
        self._by_prefix[key.prefix] = revoked
        return RevokeOutcome.REVOKED, revoked
