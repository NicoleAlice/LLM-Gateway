from datetime import UTC, datetime, timedelta
from decimal import Decimal

from llm_gateway.audit.records import AuditRecord, InMemoryAuditRepository

T0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def rec(record_id: str, tenant: str, at: datetime) -> AuditRecord:
    return AuditRecord(
        id=record_id,
        tenant_id=tenant,
        key_id="k",
        key_prefix="p",
        provider="fake",
        model="fake-echo",
        input_tokens=1,
        output_tokens=2,
        cost_usd=Decimal("0.000005"),
        status="ok",
        error_code=None,
        latency_ms=0,
        created_at=at,
    )


async def test_window_is_half_open_and_per_tenant() -> None:
    repo = InMemoryAuditRepository()
    for record in (
        rec("before", "t1", T0 - timedelta(microseconds=1)),
        rec("start", "t1", T0),
        rec("inside", "t1", T0 + timedelta(minutes=30)),
        rec("end", "t1", T0 + timedelta(hours=1)),
        rec("other-tenant", "t2", T0 + timedelta(minutes=5)),
    ):
        await repo.add(record)
    rows = await repo.list_for_tenant("t1", T0, T0 + timedelta(hours=1))
    assert [r.id for r in rows] == ["start", "inside"]


async def test_oldest_first_and_ties_keep_insertion_order() -> None:
    repo = InMemoryAuditRepository()
    for record in (
        rec("late", "t1", T0 + timedelta(seconds=5)),
        rec("a", "t1", T0),
        rec("b", "t1", T0),
    ):
        await repo.add(record)
    rows = await repo.list_for_tenant("t1", T0, T0 + timedelta(minutes=1))
    assert [r.id for r in rows] == ["a", "b", "late"]
