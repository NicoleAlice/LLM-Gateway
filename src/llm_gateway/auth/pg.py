"""PostgreSQL storage for tenants and API keys (``TenantRepository``, ``KeyRepository``)."""

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_gateway.auth.keys import Tenant
from llm_gateway.db.schema import tenants


class PgTenantRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def add(self, tenant: Tenant) -> None:
        duplicate = False
        try:
            async with self._engine.begin() as conn:
                await conn.execute(insert(tenants).values(id=tenant.id, name=tenant.name))
        except IntegrityError:  # the primary key is the table's only constraint
            duplicate = True
        # Raised here, not inside the except: the driver error stays out of __context__.
        if duplicate:
            raise ValueError(f"duplicate tenant id {tenant.id}")

    async def get(self, tenant_id: str) -> Tenant | None:
        query = select(tenants.c.id, tenants.c.name).where(tenants.c.id == tenant_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).one_or_none()
        return None if row is None else Tenant(id=row.id, name=row.name)
