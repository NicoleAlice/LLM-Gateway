"""One audit record per chat attempt that passed auth and validation, failures included."""

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


class AuditRepository(Protocol):
    async def add(self, record: AuditRecord) -> None: ...

    async def list_for_tenant(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[AuditRecord]:
        """Records with ``start <= created_at < end``, oldest first (ties: insertion order)."""
        ...


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    async def add(self, record: AuditRecord) -> None:
        self._records.append(record)

    async def list_for_tenant(
        self, tenant_id: str, start: datetime, end: datetime
    ) -> list[AuditRecord]:
        rows = [
            r for r in self._records if r.tenant_id == tenant_id and start <= r.created_at < end
        ]
        return sorted(rows, key=lambda r: r.created_at)  # stable: ties keep insertion order
