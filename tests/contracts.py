"""Repository contract tests run on every backend: the same test, the same expectations.

A test asks for ``repos`` and runs twice, once in memory and once on PostgreSQL (skipped
without a test database, failed in CI if it is missing). Tests that only make sense on
one backend check ``repos.backend``."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from llm_gateway.auth.pg import PgTenantRepository
from llm_gateway.auth.repository import InMemoryTenantRepository, TenantRepository
from llm_gateway.db.engine import make_engine
from llm_gateway.routing.pg import PgRoutingRuleRepository
from llm_gateway.routing.repository import InMemoryRoutingRuleRepository, RoutingRuleRepository
from tests.pg import TABLES


@dataclass
class Repos:
    backend: str
    tenants: TenantRepository
    routing: RoutingRuleRepository


@pytest.fixture(params=["memory", "postgres"])
async def repos(request: pytest.FixtureRequest) -> AsyncIterator[Repos]:
    if request.param == "memory":
        yield Repos("memory", InMemoryTenantRepository(), InMemoryRoutingRuleRepository())
        return
    url: str = request.getfixturevalue("pg_url")  # skips without a test database
    engine = make_engine(url, null_pool=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {TABLES}"))
        yield Repos("postgres", PgTenantRepository(engine), PgRoutingRuleRepository(engine))
    finally:
        await engine.dispose()
