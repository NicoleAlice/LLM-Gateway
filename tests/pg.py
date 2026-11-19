"""Where the PostgreSQL tests run, and how their database is reset.

``LLMGW_TEST_DATABASE_URL`` comes from the shell or ``./.env`` and is read **at import**:
the autouse ``_clean_env`` fixture strips ``LLMGW_*`` before every test. Without it the
PostgreSQL cases skip, unless ``LLMGW_REQUIRE_POSTGRES=1`` (CI), where a missing URL
stops the run instead of letting it pass on memory alone.
"""

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url

from llm_gateway.db.engine import make_engine
from llm_gateway.db.schema import metadata

ROOT = Path(__file__).resolve().parents[1]
URL_VAR = "LLMGW_TEST_DATABASE_URL"
TABLES = ", ".join(table.name for table in metadata.sorted_tables)


def _read_url() -> str | None:
    env_file = Path(".env")
    from_file = dotenv_values(env_file).get(URL_VAR) if env_file.is_file() else None
    url = (os.environ.get(URL_VAR) or from_file or "").strip()
    return url or None


TEST_DATABASE_URL = _read_url()
if TEST_DATABASE_URL is None and os.environ.get("LLMGW_REQUIRE_POSTGRES") == "1":
    raise RuntimeError(f"LLMGW_REQUIRE_POSTGRES=1 but {URL_VAR} is not set")


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["url"] = url
    return config


def reset_schema(url: str) -> None:
    """Drop everything in ``public`` and migrate to head. Refuses, before connecting,
    any database whose name does not end in ``_test``: this wipes data."""
    if not (make_url(url).database or "").endswith("_test"):
        raise ValueError("refusing to reset a database whose name does not end in _test")

    async def recreate_public() -> None:
        engine = make_engine(url, null_pool=True)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    asyncio.run(recreate_public())
    command.upgrade(alembic_config(url), "head")
