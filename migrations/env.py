"""Alembic environment: async, online only, URL from LLMGW_DATABASE_URL (or, in tests,
``config.attributes["url"]``)."""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection

from llm_gateway.db.engine import make_engine
from llm_gateway.db.schema import metadata

config = context.config


def _url() -> str:
    url = config.attributes.get("url") or os.environ.get("LLMGW_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("set LLMGW_DATABASE_URL to the database to migrate")
    return str(url)


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    engine = make_engine(_url(), null_pool=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported")
asyncio.run(_run())
