"""Initial schema: tenants, API keys, routing rules, audit records.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ordered() -> sa.Text:
    return sa.Text(collation="C")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", _ordered(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", _ordered(), nullable=False),
        sa.Column("tenant_id", _ordered(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("prefix", _ordered(), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        sa.CheckConstraint("role IN ('admin', 'developer', 'viewer')", name="ck_api_keys_role"),
    )
    op.create_index("ix_api_keys_tenant_created_id", "api_keys", ["tenant_id", "created_at", "id"])
    op.create_table(
        "routing_rules",
        sa.Column("tenant_id", _ordered(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_routing_rules"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant", ondelete="CASCADE"
        ),
        sa.CheckConstraint("mode IN ('preferred', 'mandatory')", name="ck_routing_rules_mode"),
    )
    op.create_table(
        "audit_records",
        sa.Column("id", _ordered(), nullable=False),
        sa.Column("tenant_id", _ordered(), nullable=False),
        sa.Column("key_id", _ordered(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", _ordered(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_records"),
        sa.CheckConstraint("status IN ('ok', 'error')", name="ck_audit_records_status"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0",
            name="ck_audit_records_non_negative",
        ),
    )
    op.create_index(
        "ix_audit_records_tenant_created_id", "audit_records", ["tenant_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_table("audit_records")
    op.drop_table("routing_rules")
    op.drop_table("api_keys")
    op.drop_table("tenants")
