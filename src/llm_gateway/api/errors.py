"""One error shape for every response: ``{"error": {"code": str, "message": str}}``.

Validation errors list field paths only. Pydantic's own error details include the raw
input, which could be a prompt or a secret, so they are never serialized.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("llm_gateway")

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}
TOO_LARGE_MESSAGE = "request body too large"


def _phrase(status_code: int) -> str:
    if status_code == 413:  # same text whether refused by Content-Length or while streaming
        return TOO_LARGE_MESSAGE
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:  # non-standard status: still answer in the error shape
        return "error"


class ApiError(Exception):
    """An expected error with a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_response(
    status_code: int, code: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def _field_path(error: Mapping[str, Any]) -> str:
    loc: tuple[int | str, ...] = tuple(error["loc"])
    if error["type"] == "extra_forbidden":
        # The last part is a key the client invented: name the parent, never the key.
        parent = ".".join(str(part) for part in loc[:-1]) or "request"
        return f"{parent} (unknown field)"
    return ".".join(str(part) for part in loc) or "request"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({_field_path(err) for err in exc.errors()})
        return error_response(422, "invalid_request", "invalid fields: " + ", ".join(fields))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, _phrase(exc.status_code), exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the details server-side; the client only learns that something broke.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "internal server error")
