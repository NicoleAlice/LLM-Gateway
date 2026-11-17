from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from fastapi import Request

from llm_gateway.auth.repository import (
    InMemoryKeyRepository,
    InMemoryTenantRepository,
    KeyRepository,
    TenantRepository,
)
from llm_gateway.config import Settings
from llm_gateway.routing.repository import InMemoryRoutingRuleRepository, RoutingRuleRepository


def _default_catalog() -> dict[str, str]:
    """Model name -> provider name. Real providers are added in the next plan."""
    return {"fake-echo": "fake"}


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


def get_state(request: Request) -> AppState:
    """FastAPI dependency returning the app's ``AppState``."""
    return cast(AppState, request.app.state.gateway)
