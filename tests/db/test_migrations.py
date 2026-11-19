"""The Alembic migration against a real PostgreSQL (skipped without one, failed in CI)."""

import asyncio
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Integer, Numeric, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_gateway.db.engine import make_engine
from llm_gateway.db.schema import metadata
from tests.pg import alembic_config

TABLES = {"tenants", "api_keys", "routing_rules", "audit_records"}


def _query(url: str, sql: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = make_engine(url, null_pool=True)
        try:
            async with engine.connect() as conn:
                return list((await conn.execute(text(sql))).all())
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _tables(url: str) -> set[str]:
    rows = _query(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r[0] for r in rows} - {"alembic_version"}


def test_upgrade_creates_the_four_tables(pg_url: str) -> None:
    assert _tables(pg_url) == TABLES


def test_downgrade_to_base_then_upgrade_again(pg_url: str) -> None:
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "base")
    assert _tables(pg_url) == set()
    command.upgrade(cfg, "head")
    assert _tables(pg_url) == TABLES


def test_no_drift_between_schema_and_migration(pg_url: str) -> None:
    command.check(alembic_config(pg_url))  # raises if autogenerate would emit anything


def test_the_audit_index_exists(pg_url: str) -> None:
    [(definition,)] = _query(
        pg_url,
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_records_tenant_created_id'",
    )
    assert "(tenant_id, created_at, id)" in definition


def test_compared_text_uses_the_c_collation(pg_url: str) -> None:
    rows = _query(
        pg_url,
        "SELECT table_name, column_name, collation_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND (column_name IN ('id', 'tenant_id', 'key_id', "
        "'prefix') OR (table_name = 'audit_records' AND column_name = 'model'))",
    )
    assert rows, "no columns found"
    assert {(t, c): coll for t, c, coll in rows} == {(t, c): "C" for t, c, _ in rows}


def test_schema_py_matches_the_database_where_alembic_check_cannot_see(pg_url: str) -> None:
    # `alembic check` compares tables, columns, types and indexes, but not collations or
    # CHECK constraints: compare those two by hand, so schema.py stays the truth.
    in_db = {
        (t, c): coll
        for t, c, coll in _query(
            pg_url,
            "SELECT table_name, column_name, collation_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'",
        )
    }
    in_code = {
        (table.name, column.name): getattr(column.type, "collation", None)
        for table in metadata.sorted_tables
        for column in table.columns
    }
    assert in_code == in_db
    checks_in_db = {
        r[0]
        for r in _query(
            pg_url,
            "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND contype IN ('c', 'f', 'u', 'p') "
            "AND conname <> 'alembic_version_pkc'",
        )
    }
    in_code_constraints = {
        str(constraint.name) for table in metadata.sorted_tables for constraint in table.constraints
    }
    assert in_code_constraints == checks_in_db


def test_schema_py_numeric_columns_match_the_database(pg_url: str) -> None:
    # Nor does it compare NUMERIC precision and scale.
    in_db = {
        (t, c): (p, s)
        for t, c, p, s in _query(
            pg_url,
            "SELECT table_name, column_name, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND data_type = 'numeric'",
        )
    }
    in_code = {
        (table.name, column.name): (column.type.precision, column.type.scale)
        for table in metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, Numeric) and not isinstance(column.type, Integer)
    }
    assert in_code == in_db == {("audit_records", "cost_usd"): (None, None)}


def test_cost_is_unscaled_numeric(pg_url: str) -> None:
    [(kind, precision, scale)] = _query(
        pg_url,
        "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns "
        "WHERE table_name = 'audit_records' AND column_name = 'cost_usd'",
    )
    assert (kind, precision, scale) == ("numeric", None, None)


def test_timestamps_are_timestamptz(pg_url: str) -> None:
    rows = _query(
        pg_url,
        "SELECT data_type FROM information_schema.columns WHERE table_schema = 'public' "
        "AND column_name IN ('created_at', 'revoked_at')",
    )
    assert len(rows) == 3
    assert {r[0] for r in rows} == {"timestamp with time zone"}


AUDIT_COLUMNS = (
    "id, tenant_id, key_id, key_prefix, provider, model, input_tokens, output_tokens, "
    "cost_usd, status, error_code, latency_ms, created_at"
)
GOOD = {
    "key": "INSERT INTO api_keys VALUES ('k1', 't1', 'admin', 'p1', 'h', now(), NULL)",
    "rule": "INSERT INTO routing_rules VALUES ('t1', 'preferred', 'fake-echo')",
    "audit": (
        f"INSERT INTO audit_records ({AUDIT_COLUMNS}) VALUES "
        "('a1', 't1', 'k1', 'p', 'fake', 'fake-echo', 1, 2, 0.5, 'ok', NULL, 3, now())"
    ),
}


@pytest.mark.parametrize(
    ("row", "good", "bad"),
    [
        ("key", "'admin'", "'owner'"),
        ("rule", "'preferred'", "'sometimes'"),
        ("rule", "('t1'", "('no-such-tenant'"),
        ("audit", "'ok'", "'maybe'"),
        ("audit", ", 1, 2,", ", -1, 2,"),
        ("audit", ", 1, 2,", ", 1, -2,"),
        ("audit", ", 3, now()", ", -3, now()"),
    ],
)
async def test_the_schema_rejects_impossible_rows(
    pg_engine: AsyncEngine, row: str, good: str, bad: str
) -> None:
    assert GOOD[row].count(good) == 1, "each case changes exactly one value"
    async with pg_engine.begin() as conn:
        await conn.execute(text("INSERT INTO tenants VALUES ('t1', 'acme')"))
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(text(GOOD[row].replace(good, bad)))


async def test_the_rows_those_cases_change_are_accepted(pg_engine: AsyncEngine) -> None:
    # Guards the test above: each bad row is a good row with one value changed.
    async with pg_engine.begin() as conn:
        await conn.execute(text("INSERT INTO tenants VALUES ('t1', 'acme')"))
        for sql in GOOD.values():
            await conn.execute(text(sql))


async def test_a_failed_statement_does_not_carry_its_parameters(pg_engine: AsyncEngine) -> None:
    secret = "PARAM-MARKER-must-not-be-logged"
    statement = text("INSERT INTO tenants (id, name) VALUES (:id, :name)")
    async with pg_engine.begin() as conn:
        await conn.execute(statement, {"id": "t1", "name": secret})
    with pytest.raises(IntegrityError) as excinfo:
        async with pg_engine.begin() as conn:
            await conn.execute(statement, {"id": "t1", "name": secret})
    assert secret not in str(excinfo.value)
