from datetime import UTC, datetime

from fastapi import FastAPI

from llm_gateway import __version__
from llm_gateway.api import admin, chat
from llm_gateway.api.errors import register_error_handlers
from llm_gateway.api.limits import BodySizeLimit
from llm_gateway.config import Settings
from llm_gateway.state import AppState, Clock


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    """Build the app. Run with ``uvicorn --factory llm_gateway.main:create_app``."""
    app = FastAPI(title="LLM Gateway", version=__version__)
    state = AppState(
        settings=settings if settings is not None else Settings(),
        clock=clock if clock is not None else _utc_now,
    )
    app.state.gateway = state

    register_error_handlers(app)
    app.add_middleware(BodySizeLimit, max_bytes=state.settings.max_body_bytes)
    app.include_router(admin.router)
    app.include_router(chat.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
