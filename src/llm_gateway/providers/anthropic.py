"""Claude models through the official ``anthropic`` SDK (Messages API, non-streaming).

The SDK client is built and closed by the app (see ``main.py``) and injected here, so
tests can hand in a client whose HTTP layer is a mock.

Only ``model``, ``max_tokens``, ``system`` and ``messages`` are sent: current Claude
models reject sampling parameters (``temperature``, ``top_p``, ``top_k``), and thinking
stays at the model's default (it cannot be disabled on Opus 5.5).

Raised errors carry the SDK exception's type name only, and are raised *outside* the
``except`` block so they have no ``__context__``: upstream error messages and response
bodies can quote the prompt, so they must not ride along to a log, a tracker or a
response.
"""

import logging

import anthropic
from anthropic.types import (
    AssistantMessageParam,
    Message,
    MessageParam,
    SystemMessageParam,
    UserMessageParam,
)

from llm_gateway.providers.base import (
    ChatRequest,
    ChatResponse,
    ProviderError,
    ProviderRejectedRequest,
    Usage,
)

logger = logging.getLogger("llm_gateway.providers.anthropic")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        system, messages = _split_system(request)
        try:
            reply = await self._client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=system or anthropic.omit,  # an empty system prompt is not sent at all
                messages=messages,
            )
            return self._to_response(reply)
        # Most specific first. 400/422 mean the API found the request itself invalid:
        # the caller's problem. (A gateway bug that builds a bad body would also land
        # here; 413 would too in principle, but the body limits keep requests far below
        # the API's size limit, so it is left with the other failures.)
        except (anthropic.BadRequestError, anthropic.UnprocessableEntityError) as exc:
            failure: ProviderError = ProviderRejectedRequest(
                f"anthropic rejected the request ({type(exc).__name__})"
            )
        except Exception as exc:
            # SDK errors (rate limit, overload, 5xx, auth, connection, timeout) and
            # answers the SDK couldn't parse (bad JSON, missing fields).
            failure = ProviderError(f"anthropic call failed ({type(exc).__name__})")
        raise failure

    def _to_response(self, reply: Message) -> ChatResponse:
        # Called inside chat()'s try: a malformed answer (the SDK hands back a plain
        # string for a non-JSON 200, or a message missing fields) fails here and
        # becomes a ProviderError.
        usage = reply.usage
        cached = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
        if cached:
            # The gateway sends no cache_control, so this should never happen; if it does,
            # the bill below misses these tokens. Loud, so it gets noticed and reconciled.
            logger.error(
                "anthropic reported %d cached input tokens that are not billed (model %s)",
                cached,
                reply.model,
            )
        return ChatResponse(
            provider=self.name,
            model=reply.model,
            # Only text blocks; thinking, redacted thinking and any other block are dropped.
            content="".join(block.text for block in reply.content if block.type == "text"),
            usage=Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens),
            stop_reason=reply.stop_reason or "end_turn",
        )


def _split_system(request: ChatRequest) -> tuple[str, list[MessageParam]]:
    """Leading system messages become the top-level ``system`` prompt (joined with blank
    lines); later ones stay in place as mid-conversation system messages."""
    leading: list[str] = []
    rest: list[MessageParam] = []
    for message in request.messages:
        if message.role == "system" and not rest:
            leading.append(message.content)
        elif message.role == "system":
            rest.append(SystemMessageParam(role="system", content=message.content))
        elif message.role == "assistant":
            rest.append(AssistantMessageParam(role="assistant", content=message.content))
        else:
            rest.append(UserMessageParam(role="user", content=message.content))
    return "\n\n".join(leading), rest
