"""Mutation run for plan 1b-A task A2 (/v1/usage aggregated by the repository).

Replaces Task 8's S01-S08 mutants, whose target (the aggregation inside the route) moved
into the repository. Apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
REC, USAGE = S / "audit" / "records.py", S / "api" / "usage.py"

MUTANTS = [
    ("G01 error rows counted", REC, '            if record.status == "ok":  # failures are never billed\n', "            if True:\n"),
    ("G02 not grouped by model", REC, "                by_model[record.model].append(record)\n", '                by_model["all"].append(record)\n'),
    ("G03 models unsorted", REC, "            for model, rows in sorted(by_model.items())\n", "            for model, rows in reversed(list(by_model.items()))\n"),
    ("G04 calls miscounted", REC, "                calls=len(rows),\n", "                calls=1,\n"),
    ("G05 input tokens not summed", REC, "                input_tokens=sum(r.input_tokens for r in rows),\n", "                input_tokens=rows[0].input_tokens,\n"),
    ("G06 output tokens from input", REC, "                output_tokens=sum(r.output_tokens for r in rows),\n", "                output_tokens=sum(r.input_tokens for r in rows),\n"),
    ("G07 cost summed through float", REC, "                cost_usd=sum((r.cost_usd for r in rows), Decimal(0)),\n", "                cost_usd=Decimal(str(sum(float(r.cost_usd) for r in rows))),\n"),
    ("G08 cost from first row only", REC, "                cost_usd=sum((r.cost_usd for r in rows), Decimal(0)),\n", "                cost_usd=rows[0].cost_usd,\n"),
    ("G09 tenant filter dropped (window shared)", REC, "if r.tenant_id == tenant_id and start <= r.created_at < end", "if start <= r.created_at < end"),
    ("U01 total not quantized", USAGE, "    return UsageSummary(total_cost_usd=_money(total), by_model=models)", "    return UsageSummary(total_cost_usd=str(total), by_model=models)"),
    ("U02 total from first model only", USAGE, "    total = sum((t.cost_usd for t in totals), Decimal(0))\n", "    total = sum((t.cost_usd for t in totals[:1]), Decimal(0))\n"),
    ("U03 per-model cost not quantized", USAGE, "            cost_usd=_money(t.cost_usd),\n", "            cost_usd=str(t.cost_usd),\n"),
    ("U04 usage reads the wrong tenant", USAGE, "await state.audit.usage_by_model(reader.tenant_id, start, end)", "await state.audit.usage_by_model(reader.key_id, start, end)"),
    ("U05 calls not reported", USAGE, "            calls=t.calls,\n", "            calls=0,\n"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True, timeout=300).returncode
    finally:
        path.write_text(original, encoding="utf-8", newline="\n")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
