"""Reject oversized request bodies before anything reads them.

FastAPI parses the JSON body before running auth dependencies, so without this an
anonymous caller could make the server buffer and decode a body of any size.
"""

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from llm_gateway.api.errors import TOO_LARGE_MESSAGE, error_response


class BodySizeLimit:
    """Pure ASGI middleware: 413 when a request body is larger than ``max_bytes``."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            # Fast path: refuse without reading a single body byte.
            response = error_response(413, "payload_too_large", TOO_LARGE_MESSAGE)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            # Chunked uploads have no Content-Length: count bytes as they arrive.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise StarletteHTTPException(413)
            return message

        await self.app(scope, limited_receive, send)
