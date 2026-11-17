# Plan: what's missing

Gap analysis of the repo as of 2026-11-17 and the work left, split into small tasks.
Each task is one PR: write the listed tests first, then the code until they pass.

## Where we are

| Area | State |
|---|---|
| Tooling | Done: uv, ruff, mypy strict, pytest, GitHub Actions CI |
| App | `create_app()` + `GET /health` only |
| `auth/`, `routing/`, `providers/`, `audit/`, `billing/` | Empty packages (docstrings only) |
| Database | Dependencies installed (SQLAlchemy, Alembic, psycopg), nothing wired |
| Config | `pydantic-settings` installed, no `Settings` class |
| README | Feature list and roadmap; architecture and design decisions are TODO |

So every feature in the README is still missing. That's fine, but the README should not
promise more than the code does: mark features as planned until they ship.

## Guardrails

- Written from scratch. No code, names or structure copied from current or past employers.
- No LLM-as-a-judge in this repo.
- Every PR keeps CI green (ruff, mypy strict, pytest).

---

## Phase 1: core (the demoable slice)

Goal: `curl` a chat request with an API key, get an answer from the fake provider, and see
the audit row and the bill. Everything in memory first; the database comes in task 1.8.

### 1.1 Settings
- `src/llm_gateway/config.py`: `Settings(BaseSettings)` with `database_url`, `anthropic_api_key`
  (optional, `SecretStr`), `env_prefix="LLMGW_"`.
- Tests: reads values from env vars; missing Anthropic key is allowed.

### 1.2 Provider contract + fake provider
- `providers/base.py`: `ChatMessage`, `ChatRequest`, `ChatResponse` (Pydantic), `Usage`
  (input/output tokens), and a `Provider` Protocol with `async def chat(request) -> ChatResponse`.
- `providers/fake.py`: deterministic `FakeProvider` (echoes the last message, counts tokens
  with a simple rule) plus a way to make it fail on demand.
- Tests: echo is deterministic; usage counts match the rule; forced failure raises a typed
  `ProviderError`.

### 1.3 `POST /v1/chat`
- Route that takes `ChatRequest`, calls the provider, returns `ChatResponse`.
- Provider injected via FastAPI dependency so tests swap in the fake.
- Tests: 200 with the fake; provider failure returns 502 with a stable error body (no stack trace).

### 1.4 Tenants and API keys
- `auth/`: `Tenant`, `ApiKey` (id, tenant_id, role, prefix, hash, created_at, revoked_at).
- Key format `llmgw_<prefix>_<secret>`; store only the hash; show the full key once at creation.
- Repository interface + in-memory implementation.
- Tests: created key verifies; wrong secret fails; stored record never contains the secret;
  hash comparison is constant-time.

### 1.5 Authentication + RBAC on the API
- Dependency that reads `Authorization: Bearer <key>` and resolves tenant + role.
- Roles: `admin` (manage keys), `developer` (call `/v1/chat`), `viewer` (read audit/usage).
- Admin endpoints: create key, list keys (prefix only), revoke key.
- Tests: missing/invalid key -> 401; wrong role -> 403; revoked key -> 401 on the very next
  request (instant revocation).

### 1.6 Routing rules
- `routing/`: per-tenant rules: `preferred` model (used when the request names none) and
  `mandatory` model (always used, request overridden).
- Pure function `resolve_model(tenant_rules, requested_model) -> (provider, model)`.
- Tests: table-driven cases for none / preferred / mandatory / unknown model.

### 1.7 Audit + billing
- `audit/`: one record per call: tenant, key prefix, provider, model, tokens, cost, status,
  latency, timestamp. Written for failures too.
- `billing/`: price table with `effective_from` dates; cost resolved by the call's timestamp;
  only successful calls are billable.
- Read endpoints: `GET /v1/audit`, `GET /v1/usage?from=&to=` (scoped to the caller's tenant).
- Tests: price change mid-month bills each call at its own date's price; failed call is
  audited with cost 0; tenant A never sees tenant B's rows.

### 1.8 PostgreSQL
- SQLAlchemy models + repositories behind the same interfaces as the in-memory ones;
  first Alembic migration.
- `docker-compose.yml` with Postgres for local dev.
- Tests: repository tests run against both implementations (in-memory always; Postgres in CI
  via a service container).

### 1.9 Anthropic provider
- `providers/anthropic.py` using `httpx.AsyncClient` against the Messages API; maps usage
  and errors to the gateway's types.
- Tests: `httpx.MockTransport` for success, 429 and 5xx; no real network in CI.

### 1.10 README for reviewers
- Architecture diagram (Mermaid): client -> auth -> routing -> provider -> audit/billing.
- Quickstart: `docker compose up`, create a tenant and key, one `curl` to `/v1/chat`.
- Design decisions, one short paragraph each (see below).
- CI badge; move shipped features from "planned" to done.

---

## Phase 2: FinOps and operations

| Task | Done when |
|---|---|
| 2.1 Quotas/budgets per tenant | Request over budget -> 429 with a clear body; alert threshold recorded |
| 2.2 SSE streaming on `/v1/chat` | Tokens stream; audit/billing written once the stream ends; mid-stream provider failure is audited and not billed |
| 2.3 OpenTelemetry traces | One trace per request with spans for auth, routing, provider |
| 2.4 Prometheus `/metrics` | Request count, latency histogram, tokens and cost per tenant/model |
| 2.5 Rate limiting per key | Burst over limit -> 429 with `Retry-After` |

## Phase 3: deployment

| Task | Done when |
|---|---|
| 3.1 Dockerfile (multi-stage, non-root) | Image builds in CI |
| 3.2 Compose stack (app + Postgres + Prometheus) | `docker compose up` gives a working demo |
| 3.3 Helm chart on `kind` | CI job installs the chart and hits `/health` |

## Optional, high value for AI roles

- **MCP server** exposing read-only tools (`get_usage`, `list_models`) so an agent can query
  the gateway. Recruiters keep asking for MCP.
- A second real provider (e.g. Bedrock or OpenAI-compatible) to prove the routing abstraction.

---

## Design decisions to write up

These are what reviewers read. Answer each one once the code exists:

1. Why only answered requests are billed.
2. How revocation stays instant (and what changes if keys are cached).
3. What happens when a provider fails mid-stream.
4. How prices are resolved by the call's date.
5. Why API keys are hashed and how the prefix is used.
6. Why repositories sit behind interfaces (in-memory for tests, Postgres in prod).

## Suggested order

1.1 -> 1.2 -> 1.3 -> 1.4 -> 1.5 -> 1.6 -> 1.7 -> 1.10 (README with the in-memory demo) -> 1.8 -> 1.9.
Shipping 1.10 early means the repo already reads well to a recruiter before the database work.
