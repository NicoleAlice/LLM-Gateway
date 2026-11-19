"""Mutation run for plan 1b-C task C2 (tenant and routing-rule repositories on PostgreSQL).

Needs the local test PostgreSQL (LLMGW_TEST_DATABASE_URL in the shell or ./.env).
Files are read and written as bytes so line endings are restored exactly."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
AUTH, ROUTING = S / "auth" / "pg.py", S / "routing" / "pg.py"

MUTANTS = [
    ('T01 duplicate tenant accepted', AUTH, '        if duplicate:\n            raise ValueError', '        if False:\n            raise ValueError'),
    ('T02 duplicate raised inside except', AUTH, "        except IntegrityError:  # the primary key is the table's only constraint\n            duplicate = True\n", '        except IntegrityError as exc:  # the primary key is the table\'s only constraint\n            raise ValueError(f"duplicate tenant id {tenant.id}") from exc\n'),
    ('T03 get ignores the id', AUTH, '.where(tenants.c.id == tenant_id)', ''),
    ('T04 name not stored', AUTH, 'values(id=tenant.id, name=tenant.name)', 'values(id=tenant.id, name=tenant.name[:50])'),
    ('R01 upsert keeps the old rule', ROUTING, '        upsert = upsert.on_conflict_do_update(\n            index_elements=[routing_rules.c.tenant_id],\n            set_={"mode": upsert.excluded.mode, "model": upsert.excluded.model},\n        )\n', '        upsert = upsert.on_conflict_do_nothing(index_elements=[routing_rules.c.tenant_id])\n'),
    ('R02 upsert keeps the old model', ROUTING, '"model": upsert.excluded.model}', '"model": routing_rules.c.model}'),
    ('R03 delete removes every tenant', ROUTING, 'delete(routing_rules).where(routing_rules.c.tenant_id == tenant_id)', 'delete(routing_rules)'),
    ('R04 get ignores the tenant', ROUTING, '        query = select(routing_rules.c.mode, routing_rules.c.model).where(\n            routing_rules.c.tenant_id == tenant_id\n        )\n', '        query = select(routing_rules.c.mode, routing_rules.c.model).limit(1)\n'),
    ('R05 unknown tenant swallowed', ROUTING, '        if unknown_tenant:\n            raise ValueError', '        if False:\n            raise ValueError'),
    ('R06 unknown tenant raised inside except', ROUTING, '        except IntegrityError:  # the conflict is handled, so only the foreign key is left\n            unknown_tenant = True\n', '        except IntegrityError as exc:  # the conflict is handled, so only the foreign key is left\n            raise ValueError(f"unknown tenant {tenant_id}") from exc\n'),
    ('R07 mode and model swapped', ROUTING, 'return RoutingRule(mode=cast(RoutingMode, row.mode), model=row.model)', 'return RoutingRule(mode=cast(RoutingMode, row.mode), model=row.mode)'),
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
