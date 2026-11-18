"""``POST /v1/chat``: route, price, call the provider, and audit every attempt.

Order matters: the model and its price are resolved *before* the provider is called,
so a call we couldn't bill is never made. Exactly one audit row is written per request
that passed auth and validation, after the outcome is known, shielded from cancellation.

Audit policy: if the row for an *answered* call can't be written, the request fails
closed (500 ``audit_unavailable``) and the log line carries what's needed to reconcile
the bill. On a request that already failed, an audit failure is logged and the
original error still reaches the client.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date
from decimal import Decimal
from time import perf_counter
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from llm_gateway.api.errors import ApiError
from llm_gateway.audit.records import AuditRecord, AuditStatus
from llm_gateway.auth.deps import Principal, require_role
from llm_gateway.auth.keys import Role
from llm_gateway.billing.pricing import MICRO_USD, NoPriceError, cost
from llm_gateway.providers.base import ChatRequest, ChatResponse, ProviderRejectedRequest, Usage
from llm_gateway.routing.rules import UnknownModelError, resolve_model
from llm_gateway.state import AppState, get_state

logger = logging.getLogger("llm_gateway.chat")
router = APIRouter(prefix="/v1", tags=["chat"])

ZERO_COST = Decimal(0).quantize(MICRO_USD)
AUDIT_WRITE_TIMEOUT_S = 5.0  # a hung audit store must not hold a cancelled request forever


class ChatResult(BaseModel):
    """The provider's answer plus what it cost."""

    provider: str
    model: str
    content: str
    usage: Usage
    stop_reason: str  # "refusal" means the model declined; the tokens are still billed
    cost_usd: str


@dataclass
class _Outcome:
    """What the audit row will say. It starts as a failure and only the last step of a
    fully successful call turns it into "ok", so nothing half-done is ever billed."""

    provider: str = ""
    model: str = ""
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=0, output_tokens=0))
    cost_usd: Decimal = ZERO_COST
    status: AuditStatus = "error"
    error_code: str | None = "internal_error"


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, ApiError):
        return exc.code
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "internal_error"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    state: Annotated[AppState, Depends(get_state)],
    caller: Annotated[Principal, Depends(require_role(Role.DEVELOPER))],
) -> ChatResult:
    # Before the try: without a valid timestamp there is no row to write. A clock that
    # raises or is naive is a 500 with no audit row; that is the one unaudited path.
    reading = state.clock()  # one timestamp for routing, pricing and the audit row
    if reading.tzinfo is None:
        raise RuntimeError("the clock must return timezone-aware datetimes")
    now = reading.astimezone(UTC)  # rows are stored and priced on the UTC calendar
    started = perf_counter()
    outcome = _Outcome(model=body.model or state.settings.default_model)
    failure: BaseException | None = None
    try:
        return await _answer(body, state, caller, now.date(), outcome)
    except BaseException as exc:
        failure = exc
        outcome.error_code = _error_code(exc)
        raise
    finally:
        record = AuditRecord(
            id=uuid.uuid4().hex,
            tenant_id=caller.tenant_id,
            key_id=caller.key_id,
            key_prefix=caller.key_prefix,
            provider=outcome.provider,
            model=outcome.model,
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
            cost_usd=outcome.cost_usd,
            status=outcome.status,
            error_code=outcome.error_code,
            latency_ms=int((perf_counter() - started) * 1000),
            created_at=now,
        )
        written = await _write_audit(state, record)
        if not written and failure is None:
            # Fail closed: never hand out an answer the bill doesn't know about.
            raise ApiError(500, "audit_unavailable", "the request could not be recorded")


async def _write_audit(state: AppState, record: AuditRecord) -> bool:
    """Write the row, shielded from cancellation and bounded in time. Never raises."""
    try:
        with anyio.move_on_after(AUDIT_WRITE_TIMEOUT_S, shield=True) as scope:
            await state.audit.add(record)
    except Exception:
        logger.exception("audit write failed: %s", _reconcile(record))
        return False
    if scope.cancelled_caught:
        logger.error("audit write timed out: %s", _reconcile(record))
        return False
    return True


def _reconcile(record: AuditRecord) -> str:
    # Enough to rebuild the bill by hand; no prompt content, no secrets.
    return (
        f"id={record.id} tenant={record.tenant_id} key={record.key_prefix} "
        f"model={record.model} status={record.status} code={record.error_code} "
        f"in={record.input_tokens} out={record.output_tokens} cost={record.cost_usd}"
    )


async def _answer(
    body: ChatRequest, state: AppState, caller: Principal, today: date, outcome: _Outcome
) -> ChatResult:
    rule = await state.routing.get(caller.tenant_id)
    try:
        provider_name, model = resolve_model(
            rule, body.model, state.settings.default_model, state.catalog
        )
    except UnknownModelError as exc:
        outcome.model = exc.model
        if exc.from_rule:
            raise ApiError(
                400,
                "routing_rule_invalid",
                "this tenant's routing rule names a model that is no longer available",
            ) from None
        raise ApiError(400, "unknown_model", "model is not available") from None
    outcome.model = model

    provider = state.providers.get(provider_name)
    if provider is None:  # catalog names a provider this deployment doesn't run
        raise ApiError(400, "unknown_model", "model is not available")

    try:
        price = state.prices.price_for(model, today)
    except NoPriceError:
        raise ApiError(503, "pricing_unavailable", "this model has no price today") from None

    outcome.provider = provider_name
    failure: ApiError | None = None
    try:
        raw = await provider.chat(body, model)
        # Re-validate: a buggy provider must not bill negative tokens or return junk.
        answer = ChatResponse.model_validate(raw.model_dump())
    # The exception type only, in logs and responses: upstream messages may echo prompts.
    except ProviderRejectedRequest as exc:
        # The provider says the request itself is invalid: the caller can fix it, so 400.
        logger.warning(
            "provider %s rejected a request for model %s: %s",
            provider_name,
            model,
            type(exc).__name__,
        )
        failure = ApiError(
            400, "provider_rejected_request", "the model provider rejected the request"
        )
    except Exception as exc:
        logger.warning(
            "provider %s failed for model %s: %s", provider_name, model, type(exc).__name__
        )
        failure = ApiError(502, "provider_error", "the model provider failed to answer")
    if failure is not None:
        # Raised outside the except block, so the provider's exception (and whatever
        # upstream text it holds) is not attached as __context__.
        raise failure

    amount = cost(answer.usage, price)
    result = ChatResult(
        provider=answer.provider,
        model=answer.model,
        content=answer.content,
        usage=answer.usage,
        stop_reason=answer.stop_reason,
        cost_usd=str(amount),
    )
    # Last step, after everything that could fail: only now is the call billable.
    outcome.usage, outcome.cost_usd = answer.usage, amount
    outcome.status, outcome.error_code = "ok", None
    return result
