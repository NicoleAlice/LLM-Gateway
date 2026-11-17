"""A deterministic provider for tests and local demos: no network, no cost."""

from llm_gateway.providers.base import ChatRequest, ChatResponse, ProviderError, Usage


def _words(text: str) -> int:
    return len(text.split())


class FakeProvider:
    """Echoes the last user message; counts whitespace-separated words as tokens."""

    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def chat(self, request: ChatRequest, model: str) -> ChatResponse:
        if self.fail:
            raise ProviderError("fake failure")
        last_user = next((m for m in reversed(request.messages) if m.role == "user"), None)
        if last_user is None:  # only reachable if validation was bypassed (model_construct)
            raise ProviderError("request has no user message")
        content = f"echo: {last_user.content}"
        return ChatResponse(
            provider=self.name,
            model=model,
            content=content,
            usage=Usage(
                input_tokens=sum(_words(m.content) for m in request.messages),
                output_tokens=_words(content),
            ),
        )
