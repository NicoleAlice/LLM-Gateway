import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from llm_gateway.config import Settings
from llm_gateway.main import create_app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's shell from leaking LLMGW_* or ANTHROPIC_* settings into tests
    (the SDK reads ANTHROPIC_BASE_URL, ANTHROPIC_CUSTOM_HEADERS, ANTHROPIC_LOG, ...)."""
    for var in [n for n in os.environ if n.upper().startswith(("LLMGW_", "ANTHROPIC_"))]:
        monkeypatch.delenv(var)


class MutableClock:
    """A clock tests can move forward or backward."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock(fixed_now: datetime) -> MutableClock:
    return MutableClock(fixed_now)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, admin_token="test-admin-token")


@pytest.fixture
def client(settings: Settings, clock: MutableClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client
