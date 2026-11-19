import os
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_gateway.config import Settings
from llm_gateway.db.engine import make_engine
from llm_gateway.main import create_app
from tests.pg import TABLES, TEST_DATABASE_URL, URL_VAR, reset_schema

pytest_plugins = ["tests.contracts"]  # the `repos` fixture: every repository test on every backend


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


@pytest.fixture(scope="session")
def pg_url() -> str:
    """The test database, reset and migrated once per run. Skips without one."""
    if TEST_DATABASE_URL is None:
        pytest.skip(f"PostgreSQL tests: set {URL_VAR}")
    # In a thread: the reset runs its own event loop (asyncio.run, Alembic's env.py), and
    # this fixture may be first requested from inside a running one (an async fixture).
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(reset_schema, TEST_DATABASE_URL).result()
    return TEST_DATABASE_URL


@pytest.fixture
async def pg_engine(pg_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine on the test database with every table emptied first."""
    engine = make_engine(pg_url, null_pool=True)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES}"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def client(settings: Settings, clock: MutableClock) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client
