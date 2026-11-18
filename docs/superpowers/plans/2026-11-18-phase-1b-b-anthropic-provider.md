# Phase 1b-B: Anthropic provider

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `/v1/chat` to real Claude models through the official `anthropic` Python SDK, with bounded latency, typed error mapping and correct billing, while CI never touches the network.

**Architecture:** `providers/anthropic.py` adapts the existing `Provider` protocol to `AsyncAnthropic.messages.create`. The SDK client is built once in `create_app` (only when a key is configured), closed on shutdown, and injected, so tests pass an `httpx2.MockTransport`. The chat route gains a provider timeout. Catalog and prices for the new models are registered next to the client.

**Tech Stack:** `anthropic` 2.10.0 (verified 2026-11-18 in a throwaway environment: built on `httpx2`, sends API version `2026-10-21`, raises `RateLimitError` / `OverloadedError` (a subclass of `InternalServerError`) / `BadRequestError`, and accepts `http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(...))`).

**Spec:** README roadmap "Phase 1b"; the "no provider timeout yet" note in the README API table; the Claude API reference for current models, prices and error types.

## Global Constraints

- All earlier constraints hold: one audit row per chat attempt, only answered calls billed, Decimal money, error contract, no secrets or prompt text in logs or errors.
- **No network in CI.** Every Anthropic test uses a mock transport. One opt-in live smoke test (B4) runs only when the developer explicitly enables it; it costs real money and is never run without the owner's go-ahead.
- Use the SDK, never raw HTTP. Catch the SDK's typed errors from most to least specific.
- Models and prices (Claude API reference, cached 2026-11-03): `claude-opus-5-5` $4 / $20 per million input / output tokens (the default when a model is configured), `claude-sonnet-5-5` $2 / $10. **`claude-haiku-5-5` is left out of this plan**: its price changes above 100K input tokens ($0.10 / $0.50 up to 100K, $0.50 / $2.50 beyond), and `PriceTable` has no tiers yet; adding it now would mis-bill long prompts.
- Never send `temperature`, `top_p` or `top_k` (rejected by API version `2026-10-21`), nor a `thinking` configuration (Opus 5.5 rejects disabling it). Effort stays at the model default.
- **Server-side refusal fallbacks are not enabled** (decision for the owner, see "Open decision" below).
- Same process as before: tests first, mutation script per task under `docs/mutation/`, independent review for B1 and B2.

## Review Focus

1. A provider 4xx caused by the caller's request (e.g. a malformed conversation) must be a 400 to the caller, not a 502, and never billed.
2. Overload (529), rate limit (429), 5xx, connection errors and timeouts must be 502/504, never billed, with no upstream message in the response or the log.
3. A `refusal` answer must be billed for the tokens the provider actually charged, and the caller must be able to tell it was a refusal.
4. A request that hits the provider timeout must leave exactly one audit row and return 504.
5. The API key must never appear in a log line, an error body, `repr(Settings)` or `repr` of the provider.

## Open decision (owner)

Anthropic recommends enabling server-side refusal fallbacks (`fallbacks: "default"`) for Claude Opus 5.5: if the model declines, the API re-runs the request on another model inside the same call. For a billing gateway this means the model that answered (and its price) can differ from the one routed and priced before the call. This plan **does not enable it**; enabling it later needs the audit row and the cost to follow the model that served the answer (`response.model` and `usage.iterations`).

**Decided by the owner on 2026-11-18: leave fallbacks off.** Billing always matches the routed model; a refusal comes back with `stop_reason: "refusal"` and is billed for the tokens used. Revisit as a separate task if needed.

---

### Task B1: `AnthropicProvider`

**Files:** Create `src/llm_gateway/providers/anthropic.py`, `tests/providers/test_anthropic.py`, `docs/mutation/task_b1.py`. Modify `src/llm_gateway/providers/base.py` (`ChatResponse.stop_reason`, `ProviderRejectedRequest`), `src/llm_gateway/api/chat.py` (map `ProviderRejectedRequest` to 400), `pyproject.toml` (`anthropic>=2.10`).

**Interfaces:**
- `ChatResponse.stop_reason: str = "end_turn"` (the fake keeps the default; `ChatResult` exposes it).
- `class ProviderRejectedRequest(ProviderError)`: the provider refused the request itself (HTTP 400/422).
- `AnthropicProvider(client: anthropic.AsyncAnthropic)`, `name = "anthropic"`; `chat(request, model)`:
  - leading `system` messages become the top-level `system` string (joined with blank lines); later `system` messages are passed as `role: "system"` (mid-conversation system messages, supported by Opus 5.5 / Sonnet 5.5; placement errors come back as a 400);
  - `max_tokens` from the request;
  - content = the `text` blocks joined (thinking blocks dropped); usage from `message.usage`; `stop_reason` and `model` from the message.
- Error mapping (most specific first): `BadRequestError`, `UnprocessableEntityError` → `ProviderRejectedRequest`; `RateLimitError`, `InternalServerError` (incl. `OverloadedError`), `APIConnectionError` (incl. timeouts), `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, any other `APIStatusError` → `ProviderError`. The provider never puts upstream messages in the exception text it raises.
- Chat route: `ProviderRejectedRequest` → 400 `provider_rejected_request`, audited, cost 0, provider not billed.

**Tests (mock transport):** request body shape (`model`, `max_tokens`, `system`, messages, no sampling or thinking params), API key sent only in the `Authorization: Bearer` header (SDK 2.x; 1.x used `x-api-key`, corrected during B1); text extraction skipping thinking; usage and stop_reason; `refusal` answer billed with `stop_reason: "refusal"` in the API response; each status (400, 401, 403, 404, 422, 429, 500, 529) and a connection error mapped as above, with the upstream message absent from the raised error; end-to-end `/v1/chat` through the provider with a mocked transport, audited and billed at the model's price.

---

### Task B2: Provider timeout

**Files:** Modify `src/llm_gateway/config.py` (`provider_timeout_s: float = Field(120, ge=1, le=600)`), `src/llm_gateway/api/chat.py`, README (API table: replace "there is no provider timeout yet" with the real bound), `docs/mutation/task_b2.py`.

**Interfaces:** the provider call runs inside `anyio.move_on_after(settings.provider_timeout_s)` (implemented that way instead of `fail_after`, so a `TimeoutError` the provider raises itself stays a 502); if the deadline fired and no answer came back, 504 `provider_timeout`, audited, cost 0. An answer that arrives anyway is returned and billed. After the call, `anyio.lowlevel.checkpoint_if_cancelled()` re-raises an outer cancellation a provider may have turned into an ordinary error. The SDK client gets the same timeout so it doesn't keep a socket open beyond it.

**Tests:** a hanging fake provider with a tiny timeout → 504 and one audit row; timeout bounds validated; the cancellation shield on the audit write still works (the timeout cancels the provider call, not the audit write).

Note to document: a non-streaming call cancelled by the gateway may still be completed and billed by the provider; the gateway records cost 0 because it never saw the usage. That contradicts the README's current wording "the audit never under-reports a bill", so B2 also rewrites that design-decision paragraph: the tenant is never billed for an answer it didn't get, and provider-side charges for timed-out calls are not visible to the gateway (reconcile against the provider's own usage report). The 504 audit row carries `error_code="provider_timeout"` so those rows can be found.

---

### Task B3: Wiring, catalog and prices

**Files:** Modify `src/llm_gateway/main.py` (lifespan), `src/llm_gateway/state.py`, README (Quickstart: how to enable Claude; Features table), `docs/mutation/task_b3.py`.

**Interfaces:**
- When `Settings.anthropic_api_key` is set: build `AsyncAnthropic(api_key=..., max_retries=2, timeout=provider_timeout_s)`, register `providers["anthropic"]`, catalog entries `claude-opus-5-5` and `claude-sonnet-5-5` → `"anthropic"`, and their prices effective from 2026-11-03. Close the client in the app lifespan. Pin the `anthropic` and `httpx2` loggers at `WARNING` (found in the B1 review: at DEBUG, or with `ANTHROPIC_LOG=debug`, the SDK logs request options including the prompt text).
- Without a key nothing changes: the models are absent, so asking for one is a 400 `unknown_model`.
- `create_app(..., anthropic_client: AsyncAnthropic | None = None)` lets tests inject a client with a mock transport.

**Tests:** with root logging at DEBUG, a chat call leaves no prompt text in the log; with and without a key (catalog, prices, provider present or absent); client closed on shutdown; the key never appears in logs, errors or `repr`.

---

### Task B4: Opt-in live smoke test

**Files:** Create `tests/live/test_anthropic_live.py`; modify `pyproject.toml` (marker `live`, excluded by default via `-m "not live"`), README.

One short request to `claude-opus-5-5` with `max_tokens=32`; asserts a text answer, non-zero usage and a billed audit row. Skipped unless `LLMGW_LIVE_TESTS=1` and `ANTHROPIC_API_KEY` are both set. Never run in CI. Costs a fraction of a cent per run, but it is a real charge: run it only when asked.

---

## Follow-ups found in review (not in this plan)

- **Native cancellation through the audit shield** (B2 review, pre-existing): the shielded audit write survives anyio cancellation, but a native `task.cancel()` landing during the write (uvicorn does this when the graceful-shutdown timeout runs out) passes through the shield and the row is lost. Fix candidate: write the row from a task the request doesn't own, or retry the write once on `CancelledError` and re-raise. Belongs with the PostgreSQL plan, where writes are slow enough to matter.
- **Export-window bound and routing lookup:** the bound "provider timeout + 5 s" assumes the routing lookup is instant. With PostgreSQL, bound it too (or put one deadline around the whole call path).
