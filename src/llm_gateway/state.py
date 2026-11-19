from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import cast

import anthropic
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
from llm_gateway.providers.anthropic import AnthropicProvider
from llm_gateway.providers.base import Provider
from llm_gateway.providers.fake import FakeProvider
from llm_gateway.routing.repository import InMemoryRoutingRuleRepository, RoutingRuleRepository


def _default_providers() -> dict[str, Provider]:
    return {"fake": FakeProvider()}


def _default_catalog() -> dict[str, str]:
    """Model name -> provider name. Claude models are added by ``add_anthropic``."""
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


DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"

# Claude API price list, cached 2026-11-03 (USD per million input / output tokens).
# claude-haiku-5-5 is left out on purpose: its price changes above 100K input tokens and
# PriceTable has no tiers, so it would be mis-billed on long prompts.
ANTHROPIC_PRICES = (
    Price("claude-opus-5-5", date(2026, 11, 3), Decimal("4"), Decimal("20")),
    Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10")),
)


def add_anthropic(state: AppState, client: anthropic.AsyncAnthropic) -> None:
    """Register the Anthropic provider, its models and their prices."""
    state.providers["anthropic"] = AnthropicProvider(client)
    for price in ANTHROPIC_PRICES:
        state.catalog[price.model] = "anthropic"
        state.prices.add(price)
