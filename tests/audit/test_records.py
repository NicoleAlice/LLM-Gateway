from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from llm_gateway.audit.records import (
    AuditPosition,
    AuditRecord,
    InMemoryAuditRepository,
    ModelTotals,
    position_of,
)

T0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


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
    rows = await repo.list_page("t1", T0, T0 + timedelta(hours=1), limit=100, after=None)
    assert [r.id for r in rows] == ["start", "inside"]


def billed(
    record_id: str,
    model: str,
    cost: str,
    *,
    tenant: str = "t1",
    at: datetime = T0,
    status: str = "ok",
    tokens: tuple[int, int] = (1, 2),
) -> AuditRecord:
    return replace(
        rec(record_id, tenant, at),
        model=model,
        cost_usd=Decimal(cost),
        status=status,  # type: ignore[arg-type]
        error_code=None if status == "ok" else "provider_error",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
    )


async def test_usage_by_model_counts_only_ok_and_sums_exactly() -> None:
    repo = InMemoryAuditRepository()
    for record in (
        billed("1", "a", "0.000001"),
        billed("2", "a", "0.000001"),
        billed("3", "a", "0.000001"),
        billed("4", "a", "0.000000", status="error", tokens=(0, 0)),
        billed("5", "b", "0.000001"),
    ):
        await repo.add(record)
    assert await repo.usage_by_model("t1", T0, T0 + HOUR) == [
        ModelTotals("a", 3, 3, 6, Decimal("0.000003")),
        ModelTotals("b", 1, 1, 2, Decimal("0.000001")),
    ]


async def test_usage_by_model_is_per_tenant_and_windowed() -> None:
    repo = InMemoryAuditRepository()
    for record in (
        billed("in", "a", "0.000002"),
        billed("other-tenant", "a", "9.000000", tenant="t2"),
        billed("too-early", "a", "9.000000", at=T0 - timedelta(microseconds=1)),
        billed("at-end", "a", "9.000000", at=T0 + HOUR),
    ):
        await repo.add(record)
    assert await repo.usage_by_model("t1", T0, T0 + HOUR) == [
        ModelTotals("a", 1, 1, 2, Decimal("0.000002"))
    ]
    assert await repo.usage_by_model("t3", T0, T0 + HOUR) == []


async def test_usage_by_model_sorts_models_and_never_goes_through_float() -> None:
    repo = InMemoryAuditRepository()
    for record_id, model in (("1", "zeta"), ("2", "alpha"), ("3", "mid")):
        await repo.add(billed(record_id, model, "0.000001"))
    totals = await repo.usage_by_model("t1", T0, T0 + HOUR)
    assert [t.model for t in totals] == ["alpha", "mid", "zeta"]
    # 0.1 + 0.2 in float is 0.30000000000000004: Decimal sums must stay exact.
    exact = InMemoryAuditRepository()
    await exact.add(billed("4", "m", "0.100000"))
    await exact.add(billed("5", "m", "0.200000"))
    [m] = await exact.usage_by_model("t1", T0, T0 + HOUR)
    assert m.cost_usd == Decimal("0.300000")


async def test_pages_split_ties_without_skipping_or_repeating() -> None:
    repo = InMemoryAuditRepository()
    for i in range(5):  # five rows with the SAME created_at
        await repo.add(rec(f"r{i}", "t1", T0))
    first = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=None)
    second = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=position_of(first[-1]))
    third = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=position_of(second[-1]))
    assert [r.id for r in first + second + third] == ["r0", "r1", "r2", "r3", "r4"]
    assert await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=position_of(third[-1])) == []


async def test_ties_are_ordered_by_id_not_by_insertion() -> None:
    repo = InMemoryAuditRepository()
    for record_id in ("c", "a", "b"):
        await repo.add(rec(record_id, "t1", T0))
    rows = await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=None)
    assert [r.id for r in rows] == ["a", "b", "c"]


async def test_page_order_is_time_first() -> None:
    repo = InMemoryAuditRepository()
    await repo.add(rec("a-late", "t1", T0 + timedelta(minutes=1)))
    await repo.add(rec("z-early", "t1", T0))
    rows = await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=None)
    assert [r.id for r in rows] == ["z-early", "a-late"]


async def test_page_respects_tenant_window_and_cursor_together() -> None:
    repo = InMemoryAuditRepository()
    await repo.add(rec("before", "t1", T0 - timedelta(seconds=1)))
    await repo.add(rec("a", "t1", T0))
    await repo.add(rec("other", "t2", T0))
    await repo.add(rec("b", "t1", T0 + timedelta(minutes=5)))
    await repo.add(rec("after-end", "t1", T0 + HOUR))
    # A position far before the window must not widen it.
    ancient = AuditPosition(T0 - timedelta(days=30), "")
    rows = await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=ancient)
    assert [r.id for r in rows] == ["a", "b"]


async def test_limit_caps_the_page() -> None:
    repo = InMemoryAuditRepository()
    for i in range(4):
        await repo.add(rec(str(i), "t1", T0))
    assert len(await repo.list_page("t1", T0, T0 + HOUR, limit=3, after=None)) == 3


async def test_a_slow_row_behind_the_cursor_is_missed_but_never_repeated() -> None:
    # Documented limitation: a row is timestamped when its request starts and stored when
    # it ends, so it can land behind a walk that already moved on.
    repo = InMemoryAuditRepository()
    await repo.add(rec("fast", "t1", T0 + timedelta(minutes=10)))
    page = await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=None)
    cursor = position_of(page[-1])
    await repo.add(rec("slow", "t1", T0 + timedelta(minutes=5)))  # started earlier, stored later
    assert await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=cursor) == []
    # A fresh walk sees both.
    fresh = await repo.list_page("t1", T0, T0 + HOUR, limit=10, after=None)
    assert [r.id for r in fresh] == ["slow", "fast"]
