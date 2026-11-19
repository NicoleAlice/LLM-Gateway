# Phase 1b-C: PostgreSQL storage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store tenants, API keys, routing rules and audit rows in PostgreSQL when `LLMGW_DATABASE_URL` is set, behind the existing repository Protocols, with an Alembic migration, while the in-memory store stays the default and every repository test runs against both.

**Architecture:** SQLAlchemy 2.0 Core (tables, no ORM) on an async engine with the `asyncpg` driver. One `Pg*Repository` class per Protocol, each taking the `AsyncEngine`. `create_app` builds the engine when a URL is set and disposes it on shutdown. The schema is created only by Alembic (`alembic upgrade head`), never by the app. The repository tests become contract tests, parametrized over `memory` and `postgres`.

**Tech Stack:** SQLAlchemy 2.0 async, `asyncpg`, Alembic (async `env.py`), PostgreSQL 17 (local: the EDB Windows installer; CI: a `postgres:17` service container).

**Spec:** [docs/plan.md](../../plan.md) task 1.8, the repository Protocol docstrings (`auth/repository.py`, `routing/repository.py`, `audit/records.py`), and "Follow-ups found in review" in [the Phase 1b-B plan](2026-11-18-phase-1b-b-anthropic-provider.md).

## Global Constraints

- All earlier constraints hold: one audit row per chat attempt, only answered calls billed, Decimal money, the error contract, and no secrets, prompt text or SQL parameters in logs or error bodies.
- **Driver: `asyncpg`, replacing the declared `psycopg[binary]`.** psycopg's async mode cannot run on Windows' default `ProactorEventLoop`, which is what this project is developed on.
- No route and no Protocol changes signature. The Postgres classes satisfy the Protocols exactly. The in-memory classes stay and remain the default when no URL is set.
- The app never creates or alters tables. The only schema source is `migrations/versions/`, and `alembic check` must report no drift from `llm_gateway/db/schema.py`.
- `LLMGW_DATABASE_URL` is a `SecretStr`. It must start with `postgresql+asyncpg://`, and a blank value means not configured (same validator pattern as the Anthropic key). The password never appears in `repr`, logs or error bodies.
- Text that is ordered or compared (`id` columns, `audit_records.model`) uses `COLLATE "C"`, so PostgreSQL orders exactly as Python's `sorted` does.
- `cost_usd` is unscaled `NUMERIC`, so any `Decimal` round-trips exactly. Timestamps are `TIMESTAMPTZ` and come back timezone-aware.
- `api_keys.tenant_id` has **no** foreign key: `create_tenant` writes the admin key *before* the tenant on purpose (see `admin.py`). An orphan key from a crash between the two writes is never shown to anyone, so it is unusable. `routing_rules.tenant_id` references `tenants.id`. `audit_records` has no foreign keys.
- Tests read `LLMGW_TEST_DATABASE_URL` (from the shell or `./.env`) **at import time**, because the autouse `_clean_env` fixture strips `LLMGW_*` before each test. Without it, Postgres cases are skipped, unless `LLMGW_REQUIRE_POSTGRES=1` (set in CI), in which case they fail.
- The test fixture drops and recreates the schema. It refuses any database whose name does not end in `_test`.
- Same process as before: tests first, a mutation script per task (`docs/mutation/task_c<N>.py`, run against the local PostgreSQL), an independent review of C3, C5 and C6, and the owner approves each commit.

## Review Focus

1. **Ordering differs from memory.** Python sorts by code point (`"Zeta"` before `"alpha"`), while a database collation like `en_US` sorts `"alpha"` first. Pages and usage rows must come back in the same order on both backends. Owner: C4 (test `test_order_matches_python_on_mixed_case_models_and_ids`).
2. **Timestamps change on the way back.** A key or row stored with a `-03:00` time must come back as the same instant, timezone-aware. Owner: C2 and C4 (`test_non_utc_timestamps_round_trip_as_the_same_instant`).
3. **Database down at request time.** If PostgreSQL is unreachable, auth and admin routes return `503 storage_unavailable`, and chat returns `500 audit_unavailable` (fail closed) or `503`. Nothing returns a stack trace, and the DSN password is never logged. Owner: C5 (`test_unreachable_database_is_503_without_the_password`).
4. **CI silently skipping Postgres.** With `LLMGW_REQUIRE_POSTGRES=1` and no URL, the suite fails instead of skipping. Owner: C1 (`test_required_postgres_without_a_url_fails`).
5. **Tests wiping a real database.** The schema reset refuses a database not named `*_test`. Owner: C1 (`test_reset_refuses_a_non_test_database`).

---

### Task C1: Schema, migration, settings and test harness

**Files:**
- Create: `src/llm_gateway/db/__init__.py`, `src/llm_gateway/db/schema.py`, `src/llm_gateway/db/engine.py`
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial.py`
- Create: `docker-compose.yml` (a `postgres:17` service for anyone with Docker; not needed locally)
- Create: `tests/pg.py`, `tests/db/test_migrations.py`
- Modify: `pyproject.toml` (swap `psycopg[binary]` for `asyncpg`), `src/llm_gateway/config.py`, `tests/conftest.py`, `tests/test_config.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces:
  - `db.schema.metadata: sqlalchemy.MetaData` with the tables `tenants`, `api_keys`, `routing_rules` and `audit_records`. Columns mirror the dataclasses field for field. Named constraints: `pk_api_keys`, `uq_api_keys_prefix`, `pk_audit_records`, and the index `ix_audit_records_tenant_created_id` on `(tenant_id, created_at, id)`.
  - `db.engine.make_engine(url: str, *, null_pool: bool = False) -> AsyncEngine`.
  - `Settings.database_url: SecretStr | None`.
  - `tests.pg`: `TEST_DATABASE_URL: str | None` and `reset_schema(url: str) -> None`, which drops `public`, recreates it and runs `alembic upgrade head`.
  - The fixture `pg_engine -> AsyncEngine` in `conftest.py`. It is function-scoped, uses `null_pool=True` (no connection may cross event loops), and truncates every table before the test.

- [ ] **Step 1: Write the failing tests**
  - `tests/test_config.py`:
    - The URL is read from `LLMGW_DATABASE_URL`.
    - Parametrized `"", "  "` give `None`.
    - `postgresql://…` and `sqlite://…` are rejected, and the rejected value is not in the error text.
    - `repr(Settings(...))` does not contain the password.
  - `tests/db/test_migrations.py` (Postgres only):
    - `test_upgrade_creates_the_four_tables`.
    - `test_downgrade_to_base_then_upgrade_again`.
    - `test_no_drift_between_schema_and_migration` (runs `alembic check`).
    - `test_the_audit_index_exists` (via `pg_indexes`).
    - `test_reset_refuses_a_non_test_database` (`reset_schema("…/llmgw")` raises `ValueError` before connecting).
    - `test_required_postgres_without_a_url_fails`: runs `pytest` in a subprocess with `LLMGW_REQUIRE_POSTGRES=1` and no URL, and expects a non-zero exit code.
- [ ] **Step 2: Run them and confirm they fail.** Run `.venv/Scripts/python -m pytest tests/test_config.py tests/db -q`.
- [ ] **Step 3: Implement.**
  - `env.py` uses Alembic's async template. It takes the URL from `config.attributes["url"]` (tests) or from `LLMGW_DATABASE_URL`.
  - `alembic.ini` contains no URL.
  - The missing-URL rule is enforced in `tests/pg.py` at import time: `pytest.fail` is not allowed there, so raise `RuntimeError("LLMGW_REQUIRE_POSTGRES=1 but LLMGW_TEST_DATABASE_URL is not set")`.
- [ ] **Step 4: CI.** Add a `postgres:17` service with a health check, and set the env vars `LLMGW_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/llmgw_test` and `LLMGW_REQUIRE_POSTGRES=1`.
- [ ] **Step 5: Run the full suite and the mutation script `task_c1.py`.** Mutations to include:
  - drop the `COLLATE "C"`;
  - unscale `NUMERIC` to `NUMERIC(20,6)`;
  - remove the `_test` guard;
  - remove the `REQUIRE` check;
  - accept `postgresql://`.
- [ ] **Step 6: Commit** after owner approval.

**C1 notes (done 2026-11-19):** `sqlalchemy[asyncio]` (greenlet) is required for the async engine. `alembic check` compares neither collations, CHECK/FK names nor NUMERIC precision, so `test_migrations.py` compares those between `schema.py` and the live database by hand. The engine sets `hide_parameters=True` (tested). The local database uses `Portuguese_Brazil.1252`, so the collation tests run against a real non-C locale. Mutation scripts now restore files with LF endings (the old ones left CRLF on Windows).

### Task C2: Tenant and routing-rule repositories on PostgreSQL

**Files:** Create `src/llm_gateway/auth/pg.py` (`PgTenantRepository`), `src/llm_gateway/routing/pg.py` (`PgRoutingRuleRepository`), `tests/contracts.py`, `tests/routing/test_repository.py`. Modify `tests/auth/test_repository.py`.

**Interfaces:**
- Consumes: `pg_engine` and `metadata` from C1.
- Produces:
  - `PgTenantRepository(engine: AsyncEngine)`, satisfying `TenantRepository`.
  - `PgRoutingRuleRepository(engine: AsyncEngine)`, satisfying `RoutingRuleRepository`. `set` is an upsert (`INSERT … ON CONFLICT (tenant_id) DO UPDATE`).
  - `tests.contracts`: the fixture `backend -> str`, parametrized `["memory", "postgres"]`; postgres is skipped or failed per C1's rule. Plus `tenant_repo(backend)`, `routing_repo(backend)`, and later `key_repo` and `audit_repo` factories.

- [ ] **Step 1: Convert the tenant tests and write the routing tests.**
  - Existing tenant tests take `tenant_repo` instead of building `InMemoryTenantRepository()`.
  - New routing tests:
    - `get` on an empty store is `None`.
    - `set` then `get` round-trips both modes.
    - A second `set` replaces the first.
    - `delete` is idempotent.
    - Rules are per tenant.
    - Postgres only: `set` for a tenant that does not exist raises (FK), and a duplicate tenant id raises `ValueError`.
- [ ] **Step 2: Run them; the postgres cases fail.**
- [ ] **Step 3: Implement.** Map a primary-key violation to `ValueError`, raised outside the `except` (no `__context__`, so the SQL parameters don't travel with it).
- [ ] **Step 4: Run tests, then the mutation script `task_c2.py`.** Mutations: an upsert that keeps the old rule, `delete` deleting every tenant, `get` ignoring the tenant id, and the duplicate mapped to the wrong error.
- [ ] **Step 5: Commit** after owner approval.

### Task C3: API key repository on PostgreSQL, with atomic revoke

**Files:** Modify `src/llm_gateway/auth/pg.py` (add `PgKeyRepository`), `tests/auth/test_repository.py`. Create `tests/auth/test_revoke_concurrency.py`.

**Interfaces:**
- Produces: `PgKeyRepository(engine: AsyncEngine)`, satisfying `KeyRepository`.

- [ ] **Step 1: Convert every key test in `tests/auth/test_repository.py` to the `key_repo` fixture, and add:**
  - `test_duplicate_id_is_value_error_and_duplicate_prefix_is_retryable`: tells the two errors apart by constraint name (`pk_api_keys` versus `uq_api_keys_prefix`).
  - `test_list_for_tenant_orders_by_created_at_then_id`: two keys with equal `created_at`.
  - `test_non_utc_timestamps_round_trip_as_the_same_instant`.
  - In `test_revoke_concurrency.py` (Postgres only): two active admin keys, `revoke` both concurrently with `asyncio.gather`, repeated 20 times on fresh data. Every round ends with exactly one `REVOKED`, one `LAST_ADMIN` and one active admin key.
- [ ] **Step 2: Run them; the postgres cases fail.**
- [ ] **Step 3: Implement `revoke` in one transaction.**
  - Lock the target row, then the tenant's active admin keys with `SELECT … FOR UPDATE ORDER BY id`. The fixed lock order prevents deadlocks.
  - Re-check `revoked_at IS NULL` after taking the locks, then write.
  - Return the outcomes exactly as `InMemoryKeyRepository` does.
- [ ] **Step 4: Run tests, then the mutation script `task_c3.py`.** Mutations:
  - drop `FOR UPDATE` (the concurrency test must catch this);
  - count the target key as an "other" admin;
  - drop the tenant filter in `revoke`;
  - swap the two duplicate errors;
  - `list_for_tenant` without the `id` tie-break.
- [ ] **Step 5: Independent review** (adversarial: races, tenant isolation, error leaks). Fix each finding with a test.
- [ ] **Step 6: Commit** after owner approval.

### Task C4: Audit repository on PostgreSQL

**Files:** Create `src/llm_gateway/audit/pg.py` (`PgAuditRepository`). Modify `tests/audit/test_records.py`.

**Interfaces:**
- Produces: `PgAuditRepository(engine: AsyncEngine)`, satisfying `AuditRepository`.
  - `list_page` uses `WHERE tenant_id = … AND created_at >= start AND created_at < end AND (created_at, id) > (after.created_at, after.record_id)`, then `ORDER BY created_at, id LIMIT n`.
  - `usage_by_model` is one `GROUP BY model ORDER BY model` over `status = 'ok'`.

- [ ] **Step 1: Convert all audit repository tests to the `audit_repo` fixture, and add:**
  - `test_order_matches_python_on_mixed_case_models_and_ids`: models `"Zeta"`, `"alpha"` and `"_x"`, with ids of mixed case. Pages and `usage_by_model` must equal Python's `sorted`.
  - `test_cost_round_trips_exactly`: `Decimal("0.000001")`, `Decimal("123456789.123456")`, and a sum of 1,000 micro-dollar rows.
  - `test_non_utc_timestamps_round_trip_as_the_same_instant`.
  - `test_usage_of_an_empty_window_is_empty`.
- [ ] **Step 2: Run them; the postgres cases fail.**
- [ ] **Step 3: Implement.** The sums come back as `int` and `Decimal`, never `float`. A `SUM` over `NUMERIC` is already `Decimal`, so assert the type.
- [ ] **Step 4: Run tests, then the mutation script `task_c4.py`.** Mutations: `>=` in place of `>` on the cursor, `<=` on `end`, no tenant filter, counting `error` rows, ordering by `id` only, and casting the cost to float.
- [ ] **Step 5: Commit** after owner approval.

### Task C5: Wire PostgreSQL into the app; bound the routing lookup; storage errors

**Files:**
- Modify: `src/llm_gateway/state.py` (`use_postgres`), `src/llm_gateway/main.py`, `src/llm_gateway/api/chat.py`, `src/llm_gateway/api/errors.py`, `tests/helpers.py` (`audit_rows` runs on the client's own loop through `client.portal.call`, because a Postgres repository is bound to the app's loop)
- Create: `tests/api/test_postgres_app.py`
- Modify: `README.md` (export-window bound)

**Interfaces:**
- Consumes: all `Pg*Repository` classes.
- Produces:
  - `use_postgres(state: AppState, engine: AsyncEngine) -> None`, which swaps in the four repositories.
  - `create_app(settings, clock, anthropic_client, engine: AsyncEngine | None = None)`:
    - An injected engine wins over the URL, and its caller disposes it.
    - Otherwise, a URL builds an engine that the app owns and disposes in the lifespan `finally`.
  - `chat.ROUTING_LOOKUP_TIMEOUT_S = 5.0`.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_postgres_app.py` (Postgres only, through `TestClient`):
  - `test_the_quickstart_flow_on_postgres`: tenant, keys, chat, `/v1/audit` pages, `/v1/usage`, revoke, then the revoked key gets 401.
  - `test_data_survives_a_new_app_on_the_same_database`.
  - `test_the_owned_engine_is_disposed_on_shutdown`.
  - `test_unreachable_database_is_503_without_the_password`: URL `postgresql+asyncpg://u:PASSWORD-MARKER@127.0.0.1:1/x_test`. Admin and auth routes give 503 `storage_unavailable`; chat with a stubbed key store gives the documented code. `PASSWORD-MARKER` must not be in any body or in `caplog`.
  - `test_a_slow_routing_lookup_is_503_and_audited`: in memory, with a routing repo that sleeps past a monkeypatched `ROUTING_LOOKUP_TIMEOUT_S`. Expect 503 `routing_unavailable` and exactly one error row.
  - In `tests/test_wiring.py`: without a URL, the in-memory repositories are used.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
  - The routing lookup runs inside `anyio.fail_after(ROUTING_LOOKUP_TIMEOUT_S)`.
  - Register an error handler for `sqlalchemy.exc.SQLAlchemyError` and `OSError` raised by storage. It returns `503 storage_unavailable` and logs only the exception type name.
  - Update the README bound: the export window is now provider timeout + routing lookup bound + audit write timeout.
- [ ] **Step 4: Run tests, then the mutation script `task_c5.py`.** Mutations:
  - the URL is ignored;
  - the engine is not disposed;
  - an injected engine is disposed;
  - the lookup has no deadline;
  - the handler logs `str(exc)`;
  - the handler returns 500.
- [ ] **Step 5: Independent review.** Fix each finding with a test.
- [ ] **Step 6: Commit** after owner approval.

### Task C6: An audit write survives native cancellation

The 1b-B follow-up. Today, a native `task.cancel()` that lands during the shielded audit write (uvicorn does this when its graceful-shutdown timeout runs out) loses the row.

**Files:** Modify `src/llm_gateway/api/chat.py` (`_write_audit`), `src/llm_gateway/state.py` (`AppState.pending_writes: set[asyncio.Task[None]]`), `src/llm_gateway/main.py` (drain in the lifespan). Test: `tests/api/test_audit_cancellation.py`.

**Interfaces:**
- Produces:
  - `_write_audit` runs `state.audit.add(record)` in a task held in `state.pending_writes` and awaits it through `asyncio.shield`.
  - On timeout it cancels that task and returns `False`, so no row lands after the caller was told it wasn't recorded.
  - The lifespan `finally` awaits the pending writes for at most `AUDIT_WRITE_TIMEOUT_S` and logs any that are still unfinished.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_native_cancel_during_the_write_still_stores_the_row`: a slow audit repository, the route called in a task, `task.cancel()` while the write is blocked, release the write, and exactly one row is stored.
  - `test_a_timed_out_write_never_lands_later`.
  - `test_shutdown_waits_for_a_pending_write`.
  - `test_a_finished_write_leaves_no_pending_task`.
- [ ] **Step 2: Run them; the first one fails on today's code.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run tests (with the B2 timeout tests still green), then the mutation script `task_c6.py`.** Mutations:
  - no `shield`;
  - no cancel on timeout;
  - the task is not kept in `pending_writes`;
  - no drain;
  - the drain has no deadline.
- [ ] **Step 5: Independent review.** Fix each finding with a test.
- [ ] **Step 6: Commit** after owner approval.

### Task C7: Docs

**Files:** `README.md`, `docs/plan.md`, `docs/mutation/README.md`, this plan.

- [ ] **Step 1: Update the README.**
  - Status, Features and Architecture now include PostgreSQL.
  - The quickstart covers both modes: in memory, or PostgreSQL with `alembic upgrade head`, then `LLMGW_DATABASE_URL`.
  - Add a design-decision paragraph on the atomic last-admin revoke and the missing foreign key on `api_keys`.
  - Roadmap: Phase 1b done.
  - Update the test and mutant counts.
- [ ] **Step 2: Mark tasks** 1.8 done in `docs/plan.md`. Add the C mutation scripts to the mutation table, and record the review decisions here.
- [ ] **Step 3: Run the full suite, ruff, mypy and every mutation script.**
- [ ] **Step 4: Commit** after owner approval.
