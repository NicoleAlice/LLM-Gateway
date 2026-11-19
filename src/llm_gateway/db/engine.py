from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

DRIVER_SCHEME = "postgresql+asyncpg://"


def make_engine(url: str, *, null_pool: bool = False) -> AsyncEngine:
    """An async engine for ``url`` (``postgresql+asyncpg://`` only).

    ``hide_parameters`` keeps bound values (key hashes, tenant names) out of the text of
    every SQLAlchemy error, so a logged error can't leak them. ``null_pool`` opens a fresh
    connection per use: tests need it, since a pooled asyncpg connection is tied to the
    event loop that opened it."""
    if not url.startswith(DRIVER_SCHEME):
        raise ValueError(f"the database URL must start with {DRIVER_SCHEME}")
    options: dict[str, Any] = {"hide_parameters": True, "pool_pre_ping": True}
    if null_pool:
        options["poolclass"] = NullPool
    return create_async_engine(url, **options)
