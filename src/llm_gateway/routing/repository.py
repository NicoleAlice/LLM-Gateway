"""Storage for each tenant's routing rule (at most one per tenant)."""

from typing import Protocol

from llm_gateway.routing.rules import RoutingRule


class RoutingRuleRepository(Protocol):
    async def get(self, tenant_id: str) -> RoutingRule | None: ...

    async def set(self, tenant_id: str, rule: RoutingRule) -> None:
        """Create or replace the tenant's rule."""
        ...

    async def delete(self, tenant_id: str) -> None:
        """Remove the tenant's rule; a no-op if there is none."""
        ...


class InMemoryRoutingRuleRepository:
    def __init__(self) -> None:
        self._rules: dict[str, RoutingRule] = {}

    async def get(self, tenant_id: str) -> RoutingRule | None:
        return self._rules.get(tenant_id)

    async def set(self, tenant_id: str, rule: RoutingRule) -> None:
        self._rules[tenant_id] = rule

    async def delete(self, tenant_id: str) -> None:
        self._rules.pop(tenant_id, None)
