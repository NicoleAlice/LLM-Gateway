"""PostgreSQL storage for routing rules (``RoutingRuleRepository``)."""

from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_gateway.db.schema import routing_rules
from llm_gateway.routing.rules import RoutingMode, RoutingRule


class PgRoutingRuleRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, tenant_id: str) -> RoutingRule | None:
        query = select(routing_rules.c.mode, routing_rules.c.model).where(
            routing_rules.c.tenant_id == tenant_id
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).one_or_none()
        if row is None:
            return None
        return RoutingRule(mode=cast(RoutingMode, row.mode), model=row.model)

    async def set(self, tenant_id: str, rule: RoutingRule) -> None:
        """Create or replace the rule in one statement. Raises ``ValueError`` if the tenant
        does not exist (the foreign key; the in-memory store does not check)."""
        upsert = insert(routing_rules).values(tenant_id=tenant_id, mode=rule.mode, model=rule.model)
        upsert = upsert.on_conflict_do_update(
            index_elements=[routing_rules.c.tenant_id],
            set_={"mode": upsert.excluded.mode, "model": upsert.excluded.model},
        )
        unknown_tenant = False
        try:
            async with self._engine.begin() as conn:
                await conn.execute(upsert)
        except IntegrityError:  # the conflict is handled, so only the foreign key is left
            unknown_tenant = True
        if unknown_tenant:
            raise ValueError(f"unknown tenant {tenant_id}")

    async def delete(self, tenant_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(routing_rules).where(routing_rules.c.tenant_id == tenant_id))
