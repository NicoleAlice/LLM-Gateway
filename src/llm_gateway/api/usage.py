"""Read the audit trail and what it cost: ``GET /v1/audit`` and ``GET /v1/usage``.

Both are scoped to the caller's tenant and to a half-open window ``[start, end)`` with
explicit time zones. The window is capped so one request can't scan the whole history.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel

from llm_gateway.api.errors import ApiError
from llm_gateway.audit.records import AuditRecord, AuditStatus
from llm_gateway.auth.deps import Principal, require_role
from llm_gateway.auth.keys import Role
from llm_gateway.billing.pricing import MICRO_USD
from llm_gateway.state import AppState, get_state

router = APIRouter(prefix="/v1", tags=["usage"])

MAX_WINDOW = timedelta(days=366)
State = Annotated[AppState, Depends(get_state)]
Reader = Annotated[Principal, Depends(require_role(Role.VIEWER, Role.ADMIN))]


async def time_window(
    start: Annotated[AwareDatetime, Query()], end: Annotated[AwareDatetime, Query()]
) -> tuple[datetime, datetime]:
    """Both bounds are required and must carry a time zone (a naive time is ambiguous)."""
    if start >= end:
        raise ApiError(422, "invalid_range", "start must be before end")
    if end - start > MAX_WINDOW:
        raise ApiError(422, "range_too_large", "the window can be at most 366 days")
    return start, end


Window = Annotated[tuple[datetime, datetime], Depends(time_window)]


class AuditRow(BaseModel):
    id: str
    created_at: datetime
    key_prefix: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: str
    status: AuditStatus
    error_code: str | None
    latency_ms: int

    @classmethod
    def of(cls, record: AuditRecord) -> "AuditRow":
        return cls(
            id=record.id,
            created_at=record.created_at,
            key_prefix=record.key_prefix,
            provider=record.provider,
            model=record.model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_usd=str(record.cost_usd),
            status=record.status,
            error_code=record.error_code,
            latency_ms=record.latency_ms,
        )


class ModelUsage(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: str


class UsageSummary(BaseModel):
    total_cost_usd: str
    by_model: list[ModelUsage]


def _money(amount: Decimal) -> str:
    return str(amount.quantize(MICRO_USD))


@router.get("/audit")
async def list_audit(reader: Reader, window: Window, state: State) -> list[AuditRow]:
    start, end = window
    records = await state.audit.list_for_tenant(reader.tenant_id, start, end)
    return [AuditRow.of(r) for r in records]


@router.get("/usage")
async def usage(reader: Reader, window: Window, state: State) -> UsageSummary:
    start, end = window
    records = await state.audit.list_for_tenant(reader.tenant_id, start, end)
    billed = [r for r in records if r.status == "ok"]  # failures are never billed

    by_model: dict[str, list[AuditRecord]] = defaultdict(list)
    for record in billed:
        by_model[record.model].append(record)
    models = [
        ModelUsage(
            model=model,
            calls=len(rows),
            input_tokens=sum(r.input_tokens for r in rows),
            output_tokens=sum(r.output_tokens for r in rows),
            cost_usd=_money(sum((r.cost_usd for r in rows), Decimal(0))),
        )
        for model, rows in sorted(by_model.items())
    ]
    total = sum((r.cost_usd for r in billed), Decimal(0))
    return UsageSummary(total_cost_usd=_money(total), by_model=models)
