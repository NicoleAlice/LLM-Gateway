# Phase 1b-A: Repository contracts for PostgreSQL (pagination, aggregation, atomic revoke)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the storage Protocols so a PostgreSQL implementation can be correct and bounded: paginate `/v1/audit`, aggregate `/v1/usage` inside the repository, and make "never revoke the last active admin key" a single atomic repository operation.

**Architecture:** Every change is to a Protocol plus its in-memory implementation and the route that calls it, so plan C (PostgreSQL) only implements these contracts. Audit rows get a store-assigned, strictly increasing `seq` (PostgreSQL: `bigserial`), which gives a total order `(created_at, seq)` for keyset pagination. Routes stay thin.

**Tech Stack:** unchanged (Python 3.12, FastAPI, Pydantic v2, pytest, mypy strict, ruff).

**Spec:** `README.md` roadmap "Phase 1b", the "Not paginated yet" and "Open for the Postgres plan" notes in `docs/superpowers/plans/2026-11-17-phase-1-core.md` (Tasks 4 and 8).

## Global Constraints

- All Phase 1 constraints still hold (error contract, Decimal money, UTC timestamps, one audit row per chat attempt, CI green after every task).
- Same process as Phase 1: tests first, then mutation testing of the task, then an independent adversarial review; the mutation script for each task is added under `docs/mutation/`.
- `/v1/audit` response changes from a list to `{"items": [...], "next_cursor": str | null}`. Update README (API table, quickstart if affected) in the task that makes the change.
- Cursors are opaque to clients (URL-safe base64 of a JSON object), and a malformed or tampered cursor is a 422 `invalid_cursor`, never a 500.

## Review Focus

1. A page boundary that falls between two rows with the same `created_at` must neither skip nor repeat a row.
2. A cursor taken from tenant A must not reveal or return tenant B's rows when replayed with B's key.
3. A cursor outside the requested `[start, end)` window must not widen the window.
4. Two concurrent revokes of a tenant's last two admin keys must leave at least one active admin key (contract defined here; the race itself is tested against PostgreSQL in plan C).
5. `/v1/usage` totals must stay exact (Decimal) when aggregated in the repository.

---

### Task A1: Keyset pagination

> **Changed after the A1 review (implemented as below, not as originally planned):**
> - Ties are broken by the row's own random `id`, not a store-assigned global `seq`: a global counter inside a cursor let a tenant measure every other tenant's write volume between two page requests. The order is `(created_at, id)` (PostgreSQL: index on `(tenant_id, created_at, id)`); `add` returns nothing; `list_page` returns `AuditRecord`s; `position_of(record)` builds the position; the cursor is `{"t", "i"}` and only its exact canonical encoding is accepted.
> - Converting a cursor time to UTC can raise `OverflowError` at the year-1/9999 edges; it is caught (it was a 500).
> - The cursor length is enforced by `decode_cursor`, so every bad cursor is `invalid_cursor`.
> - **Contract limitation, documented and pinned by a test:** a row is timestamped when its request starts and stored when it ends, so a slow call can land behind a walk that already passed its timestamp. Rows are never repeated but can be missed by that walk; complete exports should use a window that ended at least the longest request time plus the audit write timeout ago. PostgreSQL adds the same effect for transactions that commit out of order.
>
> The original text below is kept for history.

**Files:**
- Modify: `src/llm_gateway/audit/records.py`, `src/llm_gateway/api/usage.py`, `tests/helpers.py` (`audit_rows` reads all pages), `tests/audit/test_records.py`, `tests/api/test_usage.py`, `README.md`
- Create: `src/llm_gateway/audit/cursor.py`, `tests/audit/test_cursor.py`, `tests/api/test_audit_pages.py`, `docs/mutation/task_a1.py`

**Interfaces:**
- `@dataclass(frozen=True) class AuditPosition`: `created_at: datetime`, `seq: int`.
- `@dataclass(frozen=True) class StoredAudit`: `seq: int`, `record: AuditRecord` (what the repository returns).
- `AuditRepository.add(record) -> int` (returns the assigned `seq`).
- `AuditRepository.list_page(tenant_id, start, end, *, limit: int, after: AuditPosition | None) -> list[StoredAudit]`: rows with `start <= created_at < end` and `(created_at, seq) > after`, ordered by `(created_at, seq)`, at most `limit`.
- `audit/cursor.py`: `encode_cursor(pos: AuditPosition) -> str`, `decode_cursor(raw: str) -> AuditPosition` (raises `ValueError` on anything malformed: bad base64, bad JSON, missing keys, naive datetime, non-int or negative seq).
- `GET /v1/audit?start&end&limit=100&cursor=`: `limit` 1..1000 (default 100); response `{"items": [AuditRow...], "next_cursor": str | null}`; `next_cursor` is set only when a full page was returned and more rows may follow (fetch `limit + 1` to decide).
- `list_for_tenant` stays until A2 (the `/v1/usage` route still uses it); A2 removes it once `usage_by_model` replaces that caller, so CI stays green between tasks.

- [ ] **Step 1: Write the failing tests**

```python
# tests/audit/test_cursor.py
def test_cursor_roundtrip():
    pos = AuditPosition(datetime(2026, 11, 17, 12, tzinfo=UTC), 42)
    assert decode_cursor(encode_cursor(pos)) == pos

@pytest.mark.parametrize("raw", ["", "!!!", base64("not json"), base64('{"t": "2026-11-17T12:00:00", "s": 1}'),
                                 base64('{"t": "2026-11-17T12:00:00Z", "s": -1}'), base64('{"s": 1}')])
def test_bad_cursor_raises(raw):
    with pytest.raises(ValueError):
        decode_cursor(raw)

# tests/audit/test_records.py
async def test_pages_split_ties_without_skipping_or_repeating():
    repo = InMemoryAuditRepository()
    for i in range(5):                       # five rows with the SAME created_at
        await repo.add(rec(f"r{i}", "t1", T0))
    first = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=None)
    second = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=pos(first[-1]))
    third = await repo.list_page("t1", T0, T0 + HOUR, limit=2, after=pos(second[-1]))
    ids = [s.record.id for s in first + second + third]
    assert ids == ["r0", "r1", "r2", "r3", "r4"]

async def test_seq_is_strictly_increasing():
    repo = InMemoryAuditRepository()
    seqs = [await repo.add(rec(str(i), "t1", T0)) for i in range(3)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3

# tests/api/test_audit_pages.py
def test_pages_walk_the_whole_window(client):          # 7 calls, limit=3 -> 3 + 3 + 1, last next_cursor None
def test_default_and_max_limit(client):                # default 100; limit=1001 -> 422 invalid_request; limit=0 -> 422
def test_bad_cursor_is_422(client):                    # cursor="garbage" -> 422 invalid_cursor
def test_cursor_from_another_tenant_returns_nothing_of_theirs(client)
def test_cursor_cannot_widen_the_window(client)        # cursor positioned before `start` still only returns rows >= start
```

(Each `client` test in `test_audit_pages.py` is written in full when implementing; the comments pin the assertions.)

- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** `seq` (a counter in the in-memory repo), `list_page`, the cursor codec, the route change, README API table.
- [ ] **Step 4: Run** all checks; then `uv run python -I docs/mutation/task_a1.py .` (mutants at least: tie-break on `seq` removed, `>` vs `>=` on the position, `limit + 1` lookahead removed, cursor not checked for tz, tenant filter dropped in `list_page`, window bound ignored when `after` is set, `max limit` raised). Fix every survivor.
- [ ] **Step 5: Review + commit** `feat: keyset pagination for /v1/audit`

---

### Task A2: `/v1/usage` aggregated by the repository

**Files:**
- Modify: `src/llm_gateway/audit/records.py`, `src/llm_gateway/api/usage.py`, `tests/audit/test_records.py`
- Create: `docs/mutation/task_a2.py`

**Interfaces:**
- `@dataclass(frozen=True) class ModelTotals`: `model: str`, `calls: int`, `input_tokens: int`, `output_tokens: int`, `cost_usd: Decimal`.
- `AuditRepository.usage_by_model(tenant_id, start, end) -> list[ModelTotals]`: `status == "ok"` rows only, sorted by model (PostgreSQL: one `GROUP BY` query).
- The `/v1/usage` route only formats what the repository returns.
- Remove `AuditRepository.list_for_tenant` (its last caller is gone); `tests/helpers.audit_rows` already uses `list_page` since A1.

- [ ] **Step 1: Write the failing tests** (repository level; the existing `/v1/usage` API tests must keep passing unchanged):

```python
async def test_usage_by_model_counts_only_ok_and_sums_exactly():
    # 3 ok rows of model "a" costing 0.000001 each, 1 error row of "a", 1 ok row of "b"
    assert await repo.usage_by_model("t1", T0, T0 + HOUR) == [
        ModelTotals("a", 3, 3, 6, Decimal("0.000003")),
        ModelTotals("b", 1, 1, 2, Decimal("0.000001")),
    ]

async def test_usage_by_model_is_per_tenant_and_windowed(): ...
```

- [ ] **Steps 2-5** as in A1 (mutants: error rows counted, tenant filter, window, sort, Decimal summed through float).

---

### Task A3: Atomic revoke with the last-admin rule

**Files:**
- Modify: `src/llm_gateway/auth/repository.py`, `src/llm_gateway/api/admin.py`, `tests/auth/test_repository.py`
- Create: `docs/mutation/task_a3.py`

**Interfaces:**
- `class RevokeOutcome(StrEnum)`: `REVOKED`, `ALREADY_REVOKED`, `NOT_FOUND`, `LAST_ADMIN`.
- `KeyRepository.revoke(tenant_id, key_id, now, *, keep_one_admin: bool = True) -> tuple[RevokeOutcome, ApiKey | None]`: one atomic operation (in memory: no `await` inside; PostgreSQL: one transaction with `SELECT ... FOR UPDATE` on the tenant's active admin keys).
- The `DELETE /v1/admin/keys/{key_id}` route only maps outcomes to 204 / 204 / 404 / 409; it no longer lists keys itself.

- [ ] **Step 1: Write the failing tests**

```python
async def test_revoke_outcomes():               # REVOKED, then ALREADY_REVOKED (revoked_at kept), NOT_FOUND for other tenant
async def test_last_admin_is_refused_atomically():
    # two active admins; revoke one -> REVOKED; revoke the other -> LAST_ADMIN and it stays active
async def test_route_no_longer_checks_on_its_own(client):
    # the DELETE route maps outcomes only: patch keys.revoke to return LAST_ADMIN -> 409,
    # NOT_FOUND -> 404, ALREADY_REVOKED -> 204, without the route listing keys
```

(A real concurrency test is meaningless in memory: with no `await` inside `revoke`, two calls can't interleave. The race test, two concurrent DELETEs of the last two admin keys leaving exactly one active, belongs to plan C and runs against PostgreSQL.)

- [ ] **Steps 2-5** as in A1 (mutants: `keep_one_admin` ignored, counting revoked admins, non-admin target treated as admin, outcome mapping swapped in the route).

---

## Status

Plan A is complete: A1 (`6392241`), A2 (`aa042e7`), A3 (this commit). Implemented as specified, except the A1 changes noted in that task.

## Plans B and C (outline, written after A ships)

- **B: Anthropic provider** using the official `anthropic` SDK (`AsyncAnthropic`), not raw HTTP. Catalog adds `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` with prices from the Claude API reference at implementation time. Maps SDK typed errors (rate limit, overloaded/5xx, connection) to `ProviderError`; a `stop_reason == "refusal"` answer is not billed as `ok`. Tests inject a mock transport into the SDK client; no network in CI. An opt-in smoke test runs only when `ANTHROPIC_API_KEY` is set.
- **C: PostgreSQL** (keep A1's contract: order and cursor on `(created_at, id)`, never a global sequence in a cursor; store `id` as `uuid` or compare it with `COLLATE "C"` so the database orders ties exactly like Python's string comparison; document the out-of-order-commit skip in the repository docstring) with SQLAlchemy 2 async + psycopg 3, Alembic migrations, repositories implementing the A contracts; repository tests run against both implementations; PostgreSQL tests run in CI through a service container and are skipped locally unless `LLMGW_TEST_DATABASE_URL` is set; the audit-shield test runs against the real database.
