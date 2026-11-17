"""Storage for tenants and API keys.

Repositories are async Protocols so a Postgres implementation can replace the in-memory
one without changing any route.
"""

import dataclasses
from datetime import datetime
from typing import Protocol

from llm_gateway.auth.keys import ApiKey, Tenant


class DuplicateKeyPrefixError(Exception):
    """Another key already uses this prefix. Callers regenerate the key and retry."""


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

    async def revoke(self, tenant_id: str, key_id: str, now: datetime) -> ApiKey | None:
        """Revoke a key of *this* tenant. ``None`` if it doesn't exist or belongs to
        another tenant. Revoking twice keeps the first ``revoked_at``."""
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

    async def revoke(self, tenant_id: str, key_id: str, now: datetime) -> ApiKey | None:
        prefix = self._prefix_by_id.get(key_id)
        key = self._by_prefix.get(prefix) if prefix is not None else None
        if key is None or key.tenant_id != tenant_id:
            return None
        if key.revoked_at is not None:
            return key
        revoked = dataclasses.replace(key, revoked_at=now)
        self._by_prefix[key.prefix] = revoked
        return revoked
