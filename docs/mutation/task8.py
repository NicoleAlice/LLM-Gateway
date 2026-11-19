"""Mutation run for LLM-Gateway Task 8. Each mutant: apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
U = ROOT / "src" / "llm_gateway" / "api" / "usage.py"
MAIN = ROOT / "src" / "llm_gateway" / "main.py"

MUTANTS = [
    ("W01 start>=end allowed", U, "    if start >= end:\n", "    if False:\n"),
    ("W02 only start>end rejected", U, "    if start >= end:\n", "    if start > end:\n"),
    ("W03 range code wrong", U, '"invalid_range"', '"bad_range"'),
    ("W04 window cap removed", U, "    if end - start > MAX_WINDOW:\n", "    if False:\n"),
    ("W05 cap off by one", U, "    if end - start > MAX_WINDOW:\n", "    if end - start >= MAX_WINDOW:\n"),
    ("W06 cap bigger", U, "MAX_WINDOW = timedelta(days=366)", "MAX_WINDOW = timedelta(days=3660)"),
    ("W07 naive dates accepted", U, "start: Annotated[AwareDatetime, Query()], end: Annotated[AwareDatetime, Query()]", "start: Annotated[datetime, Query()], end: Annotated[datetime, Query()]"),
    ("R01 developers can read", U, "Depends(require_role(Role.VIEWER, Role.ADMIN))", "Depends(require_role(*Role))"),
    ("R02 admins cannot read", U, "Depends(require_role(Role.VIEWER, Role.ADMIN))", "Depends(require_role(Role.VIEWER))"),
    ("A01 audit reads the wrong tenant", U, "reader.tenant_id, start, end, limit=limit + 1, after=after", "reader.key_id, start, end, limit=limit + 1, after=after"),
    ("A02 audit cost via float", U, "            cost_usd=str(record.cost_usd),\n", "            cost_usd=str(float(record.cost_usd)),\n"),
    ("A03 audit key prefix wrong", U, "            key_prefix=record.key_prefix,\n", "            key_prefix=record.key_id,\n"),
    ("A04 audit drops error code", U, "            error_code=record.error_code,\n", "            error_code=None,\n"),
    ("A05 audit window ignored", U, "reader.tenant_id, start, end, limit=limit + 1, after=after", 'reader.tenant_id, __import__("datetime").datetime.min.replace(tzinfo=__import__("datetime").UTC), __import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").UTC), limit=limit + 1, after=after'),
    ("M01 router not mounted", MAIN, "    app.include_router(usage.router)\n", ""),
    ("T01 clock not normalized to UTC", ROOT / "src" / "llm_gateway" / "api" / "chat.py", "    now = reading.astimezone(UTC)", "    now = reading"),
    ("T02 naive clock accepted", ROOT / "src" / "llm_gateway" / "api" / "chat.py", "    if reading.tzinfo is None:\n", "    if False:\n"),
    ("T03 audit record accepts naive", ROOT / "src" / "llm_gateway" / "audit" / "records.py", "        if self.created_at.tzinfo is None:\n", "        if False:\n"),
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
