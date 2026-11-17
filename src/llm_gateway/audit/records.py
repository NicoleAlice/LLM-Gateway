"""One audit record per chat attempt that passed auth and validation, failures included."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

AuditStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class AuditRecord:
    id: str
    tenant_id: str
    key_id: str
    key_prefix: str
    provider: str  # "" when routing failed before a provider was chosen
    model: str  # the model routing chose, even when it turned out to be unknown
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal  # 0 for every failed attempt: only answered calls are billed
    status: AuditStatus
    error_code: str | None
    latency_ms: int
    created_at: datetime

    def __post_init__(self) -> None:
        # A naive timestamp can't be compared with the aware window bounds: one such row
        # would break every read of its tenant's audit trail.
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, order=True)
class AuditPosition:
    """A point in the total order ``(created_at, id)`` used by keyset pagination.

    Ties are broken by the record's own random ``id`` (which the caller already sees),
    not by a global sequence: a global counter in a cursor would reveal how much every
    other tenant writes between two page requests."""

    created_at: datetime
    record_id: str


def position_of(record: AuditRecord) -> AuditPosition:
    return AuditPosition(record.created_at, record.id)


@dataclass(frozen=True)
class ModelTotals:
    """Billed usage of one model in a window: answered calls only."""

    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class AuditRepository(Protocol):
    async def add(self, record: AuditRecord) -> None: ...

    async def list_page(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        *,
        limit: int,
        after: AuditPosition | None,
    ) -> list[AuditRecord]:
        """Up to ``limit`` rows with ``start <= created_at < end`` and a position strictly
        after ``after``, ordered by ``(created_at, id)`` (PostgreSQL: an index on
        ``(tenant_id, created_at, id)``). The window always applies: a position outside
        it can't widen it.

        A walk returns rows already stored when it passed their position. A row is
        timestamped when its request *starts* and stored when it *ends*, so a slow call
        can land behind a cursor that already moved on: it is never repeated, but it can
        be missed. For a complete export, use a window whose ``end`` is older than the
        longest request plus the audit write timeout."""
        ...

    async def usage_by_model(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[ModelTotals]:
        """Totals per model of ``status == "ok"`` rows in ``[start, end)``, sorted by model.
        Aggregated by the store (PostgreSQL: one ``GROUP BY``), never by loading rows."""
        ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    async def add(self, record: AuditRecord) -> None:
        self._records.append(record)

    def _window(self, tenant_id: str, start: datetime, end: datetime) -> list[AuditRecord]:
        rows = [
            r for r in self._records if r.tenant_id == tenant_id and start <= r.created_at < end
        ]
        return sorted(rows, key=position_of)

    async def list_page(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        *,
        limit: int,
        after: AuditPosition | None,
    ) -> list[AuditRecord]:
        rows = self._window(tenant_id, start, end)
        if after is not None:
            rows = [r for r in rows if position_of(r) > after]
        return rows[:limit]

    async def usage_by_model(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[ModelTotals]:
        by_model: dict[str, list[AuditRecord]] = defaultdict(list)
        for record in self._window(tenant_id, start, end):
            if record.status == "ok":  # failures are never billed
                by_model[record.model].append(record)
        return [
            ModelTotals(
                model=model,
                calls=len(rows),
                input_tokens=sum(r.input_tokens for r in rows),
                output_tokens=sum(r.output_tokens for r in rows),
                cost_usd=sum((r.cost_usd for r in rows), Decimal(0)),
            )
            for model, rows in sorted(by_model.items())
        ]
