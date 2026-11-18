"""The contract every LLM provider implements, and the request/response shapes it uses."""

import unicodedata
from typing import Annotated, Literal, Protocol, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _not_blank(text: str) -> str:
    # Validate without rewriting: indentation and trailing newlines are part of a prompt.
    # Format characters (zero-width space, BOM, word joiner) don't count as content.
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    if not visible.strip():
        raise ValueError("content must not be blank")
    return text


Content = Annotated[str, Field(max_length=32_000), AfterValidator(_not_blank)]

# Requests are immutable and reject unknown fields: a client sending `stream: true`
# gets a 422 instead of a silent non-streaming answer.
_REQUEST_CONFIG = ConfigDict(extra="forbid", frozen=True)


class ChatMessage(BaseModel):
    model_config = _REQUEST_CONFIG

    role: Literal["system", "user", "assistant"]
    content: Content


class ChatRequest(BaseModel):
    model_config = _REQUEST_CONFIG

    model: str | None = Field(None, min_length=1, max_length=100)
    messages: list[ChatMessage] = Field(max_length=100)
    max_tokens: int = Field(1024, ge=1, le=8192, strict=True)

    @model_validator(mode="after")
    def _has_user_message(self) -> Self:
        # Also rejects an empty list, so `messages` needs no separate min_length.
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("at least one user message is required")
        return self


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ChatResponse(BaseModel):
    provider: str
    model: str
    content: str
    usage: Usage
    # Why the model stopped, as the provider reports it ("end_turn", "max_tokens",
    # "refusal", ...). A refusal is still an answer: it is billed for the tokens used.
    stop_reason: str = "end_turn"


class ProviderError(Exception):
    """The provider could not answer (network, upstream error, refusal to serve)."""


class ProviderRejectedRequest(ProviderError):
    """The provider refused the request itself as invalid (HTTP 400/422): the caller's
    fault, so a 400 rather than a 502. Nothing was answered, so nothing is billed."""


class Provider(Protocol):
    # A read-only property, so implementations may use a plain, Final or ClassVar attribute.
    @property
    def name(self) -> str: ...

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse: ...
