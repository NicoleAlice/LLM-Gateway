from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from fastapi import Request

from llm_gateway.audit.records import AuditRepository, InMemoryAuditRepository
from llm_gateway.auth.repository import (
    InMemoryKeyRepository,
    InMemoryTenantRepository,
    KeyRepository,
    TenantRepository,
)
from llm_gateway.billing.pricing import Price, PriceTable
from llm_gateway.config import Settings
from llm_gateway.providers.base import Provider
from llm_gateway.providers.fake import FakeProvider
from llm_gateway.routing.repository import InMemoryRoutingRuleRepository, RoutingRuleRepository


def _default_providers() -> dict[str, Provider]:
    return {"fake": FakeProvider()}


def _default_catalog() -> dict[str, str]:
    """Model name -> provider name. Real providers are added in the next plan."""
    return {"fake-echo": "fake"}


def _default_prices() -> PriceTable:
    return PriceTable([Price("fake-echo", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00"))])


Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware UTC datetime."""


@dataclass
class AppState:
    """Everything a request handler needs, built once per app by ``create_app``."""

    settings: Settings
    clock: Clock
    tenants: TenantRepository = field(default_factory=InMemoryTenantRepository)
    keys: KeyRepository = field(default_factory=InMemoryKeyRepository)
    routing: RoutingRuleRepository = field(default_factory=InMemoryRoutingRuleRepository)
    catalog: dict[str, str] = field(default_factory=_default_catalog)
    prices: PriceTable = field(default_factory=_default_prices)
    providers: dict[str, Provider] = field(default_factory=_default_providers)
    audit: AuditRepository = field(default_factory=InMemoryAuditRepository)


def get_state(request: Request) -> AppState:
    """FastAPI dependency returning the app's ``AppState``."""
    return cast(AppState, request.app.state.gateway)
