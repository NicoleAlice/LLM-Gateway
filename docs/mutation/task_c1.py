"""Mutation run for plan 1b-C task C1 (schema, migration, settings, test harness).

Needs the local test PostgreSQL (LLMGW_TEST_DATABASE_URL in the shell or ./.env). Each
mutant also resets the test database, since several change the migration itself.
Files are read and written as bytes so line endings are restored exactly."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
MIG = ROOT / "migrations" / "versions" / "0001_initial.py"
SCHEMA, ENGINE, CFG = S / "db" / "schema.py", S / "db" / "engine.py", S / "config.py"
PG, CONFTEST = ROOT / "tests" / "pg.py", ROOT / "tests" / "conftest.py"

MUTANTS = [
    ("M01 migration: no C collation", MIG, 'return sa.Text(collation="C")', "return sa.Text()"),
    ("M02 schema.py: no C collation", SCHEMA, 'return Text(collation="C")', "return Text()"),
    ("M03 migration: scaled cost", MIG, 'sa.Column("cost_usd", sa.Numeric(), nullable=False)', 'sa.Column("cost_usd", sa.Numeric(20, 6), nullable=False)'),
    ("M04 schema.py: scaled cost", SCHEMA, "Numeric(asdecimal=True)", "Numeric(20, 6, asdecimal=True)"),
    ("M05 migration: no audit index", MIG, '    op.create_index(\n        "ix_audit_records_tenant_created_id",', '    (lambda *a: None)(\n        "ix_audit_records_tenant_created_id",'),
    ("M06 migration: no role check", MIG, "        sa.CheckConstraint(\"role IN ('admin', 'developer', 'viewer')\", name=\"ck_api_keys_role\"),\n", ""),
    ("M07 migration: no routing FK", MIG, '        sa.ForeignKeyConstraint(\n            ["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant", ondelete="CASCADE"\n        ),\n', ""),
    ("M08 migration: negative tokens allowed", MIG, '"input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0"', '"input_tokens >= 0 AND latency_ms >= 0"'),
    ("M09 migration: naive key timestamps", MIG, '        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),', '        sa.Column("revoked_at", sa.DateTime(), nullable=True),'),
    ("M10 migration: downgrade keeps tenants", MIG, '    op.drop_table("tenants")\n', ""),
    ("M11 schema.py: no prefix unique", SCHEMA, '    UniqueConstraint("prefix", name="uq_api_keys_prefix"),\n', ""),
    ("H01 reset guard removed", PG, '    if not (make_url(url).database or "").endswith("_test"):', "    if False:"),
    ("H02 REQUIRE ignored", PG, 'if TEST_DATABASE_URL is None and os.environ.get("LLMGW_REQUIRE_POSTGRES") == "1":', "if False:"),
    ("H03 .env not read", PG, "    from_file = dotenv_values(env_file).get(URL_VAR) if env_file.is_file() else None\n", "    from_file = None\n"),
    ("H04 tables not emptied", CONFTEST, '        await conn.execute(text(f"TRUNCATE {TABLES}"))\n', "        pass\n"),
    ("C01 any driver accepted", CFG, "        if not url.get_secret_value().startswith(DRIVER_SCHEME):\n", "        if False:\n"),
    ("C02 blank URL kept", CFG, "        if url is None or not url.get_secret_value():\n", "        if url is None:\n"),
    ("E01 parameters in errors", ENGINE, '"hide_parameters": True', '"hide_parameters": False'),
    ("E02 engine accepts any driver", ENGINE, "    if not url.startswith(DRIVER_SCHEME):\n", "    if False:\n"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", "-p", "no:cacheprovider"]
results = []
for mid, path, old, new in MUTANTS:
    original = path.read_bytes()
    text = original.decode("utf-8")
    assert text.count(old) == 1, f"{mid}: pattern not found exactly once"
    assert old != new, f"{mid}: no-op"
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True, timeout=600).returncode
    finally:
        path.write_bytes(original)
    results.append(mid)
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}", flush=True)
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
