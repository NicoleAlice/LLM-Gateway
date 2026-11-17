from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import Request

from llm_gateway.config import Settings

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware UTC datetime."""


@dataclass
class AppState:
    """Everything a request handler needs, built once per app by ``create_app``."""

    settings: Settings
    clock: Clock


def get_state(request: Request) -> AppState:
    """FastAPI dependency returning the app's ``AppState``."""
    return cast(AppState, request.app.state.gateway)
