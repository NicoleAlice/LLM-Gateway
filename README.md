# LLM Gateway

A multi-tenant gateway that sits between applications and LLM providers and controls **who can call which model, how much they can spend, and what happened on every call**.

> Status: early development. See the roadmap below.

## Features

- **Tenants, API keys and RBAC**: hashed keys, roles (`admin`, `developer`, `viewer`), instant revocation
- **Model routing**: per-tenant preferred / mandatory model rules
- **Audit trail**: every interaction recorded per tenant (model, provider, tokens, cost)
- **Usage-based billing**: date-effective pricing; only answered requests are billed
- **Quotas and budgets** (AI FinOps): per-tenant limits with alerts
- **Streaming** (SSE) and **observability** (OpenTelemetry traces, Prometheus metrics)

## Architecture

<!-- TODO: diagram (client → auth → routing → provider → audit/billing) -->

## Design decisions

<!-- The most important section for reviewers. One short paragraph each, e.g.:
- Why bill only answered requests
- How revocation stays instant even with key caching
- What happens when a provider fails mid-stream
- How prices are resolved by the interaction's date
-->

## Running locally

```bash
uv sync
uv run uvicorn llm_gateway.main:app --reload
uv run pytest
```

## Roadmap

- [ ] **Phase 1: core**: tenants, API keys, RBAC, revocation, `/v1/chat`, routing rules, audit, billing, fake + Anthropic providers
- [ ] **Phase 2: FinOps and operations**: quotas/budgets, SSE streaming, OpenTelemetry, Prometheus metrics
- [ ] **Phase 3: deployment**: Docker Compose, Helm chart tested on `kind`

## Tech stack

Python 3.12 · FastAPI · Pydantic · SQLAlchemy · Alembic · PostgreSQL · pytest · uv · ruff · mypy · GitHub Actions
