import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import anthropic
from fastapi import FastAPI

from llm_gateway import __version__
from llm_gateway.api import admin, chat, usage
from llm_gateway.api.errors import register_error_handlers
from llm_gateway.api.limits import BodySizeLimit
from llm_gateway.config import Settings
from llm_gateway.state import DEFAULT_CLAUDE_MODEL, AppState, Clock, add_anthropic

ANTHROPIC_MAX_RETRIES = 2
# Explicit, so an ANTHROPIC_BASE_URL left in the shell (proxies, other tools) can't send
# the gateway's key somewhere else.
ANTHROPIC_BASE_URL = "https://api.anthropic.com"

# At DEBUG (or with ANTHROPIC_LOG=debug) the SDK logs each request's options, prompt
# text included. Pinned here, after the SDK's own logging setup ran at import time.
_QUIET_LOGGERS = ("anthropic", "httpx2")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    anthropic_client: anthropic.AsyncAnthropic | None = None,
) -> FastAPI:
    """Build the app. Run with ``uvicorn --factory llm_gateway.main:create_app``.

    Claude is enabled when ``LLMGW_ANTHROPIC_API_KEY`` is set (the app then builds the
    SDK client and closes it on shutdown) or when a client is injected (tests; the
    caller owns and closes it). With Claude enabled and no explicit
    ``LLMGW_DEFAULT_MODEL``, the default model is ``claude-opus-5-5``.

    The client is built here and closed when the lifespan ends, so run each app through
    one lifespan only (a second start would find the client closed).
    """
    settings = settings if settings is not None else Settings()
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    owned: anthropic.AsyncAnthropic | None = None
    if anthropic_client is None and settings.anthropic_api_key is not None:
        owned = anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            base_url=ANTHROPIC_BASE_URL,
            max_retries=ANTHROPIC_MAX_RETRIES,
            # Same bound as the route's deadline, so no socket outlives a cut-off call.
            timeout=settings.provider_timeout_s,
        )
    if anthropic_client is not None and "default_model" not in settings.model_fields_set:
        settings = settings.model_copy(update={"default_model": DEFAULT_CLAUDE_MODEL})

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned is not None:
                await owned.close()

    app = FastAPI(title="LLM Gateway", version=__version__, lifespan=lifespan)
    state = AppState(settings=settings, clock=clock if clock is not None else _utc_now)
    if anthropic_client is not None:
        add_anthropic(state, anthropic_client)
    app.state.gateway = state

    register_error_handlers(app)
    app.add_middleware(BodySizeLimit, max_bytes=state.settings.max_body_bytes)
    app.include_router(admin.router)
    app.include_router(chat.router)
    app.include_router(usage.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
