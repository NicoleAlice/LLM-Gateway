from fastapi import FastAPI

from llm_gateway import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="LLM Gateway", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
