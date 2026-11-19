"""The tables, mirroring the domain dataclasses field for field.

Only Alembic creates them (``migrations/versions/``); ``alembic check`` keeps the two in
step. Text that is ordered or compared uses ``COLLATE "C"`` so PostgreSQL orders rows
exactly as Python's ``sorted`` does: a locale collation puts ``"alpha"`` before ``"Zeta"``,
Python puts it after, and pages would differ between the two stores.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


def _ordered() -> Text:
    return Text(collation="C")


tenants = Table(
    "tenants",
    metadata,
    Column("id", _ordered(), nullable=False),
    Column("name", Text, nullable=False),
    PrimaryKeyConstraint("id", name="pk_tenants"),
)

# No foreign key to tenants: the admin key is written before its tenant (see
# api/admin.py), and a key orphaned by a crash between the two writes was never shown.
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", _ordered(), nullable=False),
    Column("tenant_id", _ordered(), nullable=False),
    Column("role", Text, nullable=False),
    Column("prefix", _ordered(), nullable=False),
    Column("secret_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("id", name="pk_api_keys"),
    UniqueConstraint("prefix", name="uq_api_keys_prefix"),
    CheckConstraint("role IN ('admin', 'developer', 'viewer')", name="ck_api_keys_role"),
    Index("ix_api_keys_tenant_created_id", "tenant_id", "created_at", "id"),
)

routing_rules = Table(
    "routing_rules",
    metadata,
    Column("tenant_id", _ordered(), nullable=False),
    Column("mode", Text, nullable=False),
    Column("model", Text, nullable=False),
    PrimaryKeyConstraint("tenant_id", name="pk_routing_rules"),
    ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant", ondelete="CASCADE"
    ),
    CheckConstraint("mode IN ('preferred', 'mandatory')", name="ck_routing_rules_mode"),
)

# No foreign keys: audit rows are the bill and must outlive anything they point at.
audit_records = Table(
    "audit_records",
    metadata,
    Column("id", _ordered(), nullable=False),
    Column("tenant_id", _ordered(), nullable=False),
    Column("key_id", _ordered(), nullable=False),
    Column("key_prefix", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("model", _ordered(), nullable=False),
    Column("input_tokens", BigInteger, nullable=False),
    Column("output_tokens", BigInteger, nullable=False),
    # Unscaled NUMERIC: any Decimal round-trips exactly, nothing is silently rounded.
    Column("cost_usd", Numeric(asdecimal=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("error_code", Text, nullable=True),
    Column("latency_ms", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="pk_audit_records"),
    CheckConstraint("status IN ('ok', 'error')", name="ck_audit_records_status"),
    CheckConstraint(
        "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0",
        name="ck_audit_records_non_negative",
    ),
    Index("ix_audit_records_tenant_created_id", "tenant_id", "created_at", "id"),
)
