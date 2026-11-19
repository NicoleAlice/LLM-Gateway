"""Mutation run for plan 1b-A task A1 (keyset pagination). Apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
REC, CUR, USAGE = S / "audit" / "records.py", S / "audit" / "cursor.py", S / "api" / "usage.py"

MUTANTS = [
    ("P01 ties dropped across pages", REC, "rows = [r for r in rows if position_of(r) > after]", "rows = [r for r in rows if r.created_at > after.created_at]"),
    ("P02 cursor row repeated", REC, "rows = [r for r in rows if position_of(r) > after]", "rows = [r for r in rows if position_of(r) >= after]"),
    ("P03 no id tie-break", REC, "        return sorted(rows, key=position_of)\n", "        return sorted(rows, key=lambda r: r.created_at)\n"),
    ("P04 tenant filter dropped", REC, "if r.tenant_id == tenant_id and start <= r.created_at < end", "if start <= r.created_at < end"),
    ("P05 cursor widens the window", REC, "        rows = self._window(tenant_id, start, end)\n        if after is not None:", "        rows = self._window(tenant_id, start if after is None else after.created_at, end)\n        if after is not None:"),
    ("P06 limit ignored", REC, "        return rows[:limit]\n", "        return rows\n"),
    ("P07 window end closed", REC, "start <= r.created_at < end", "start <= r.created_at <= end"),
    ("R01 no lookahead row", USAGE, "limit=limit + 1, after=after", "limit=limit, after=after"),
    ("R02 cursor on a full final page", USAGE, "if len(rows) > limit else None", "if len(page) == limit else None"),
    ("R03 max page raised", USAGE, "DEFAULT_PAGE, MAX_PAGE = 100, 1000", "DEFAULT_PAGE, MAX_PAGE = 100, 5000"),
    ("R04 default page changed", USAGE, "DEFAULT_PAGE, MAX_PAGE = 100, 1000", "DEFAULT_PAGE, MAX_PAGE = 50, 1000"),
    ("R05 cursor error code", USAGE, '"invalid_cursor"', '"invalid_request"'),
    ("R06 next cursor from first row", USAGE, "encode_cursor(position_of(page[-1]))", "encode_cursor(position_of(page[0]))"),
    ("R07 limit lower bound", USAGE, "Query(ge=1, le=MAX_PAGE)", "Query(ge=0, le=MAX_PAGE)"),
    ("C02 overflow escapes as 500", CUR, "    except (ValueError, OverflowError) as exc:\n        raise ValueError(\"bad cursor time\")", "    except ValueError as exc:\n        raise ValueError(\"bad cursor time\")"),
    ("C03 id format not checked", CUR, "if not isinstance(record_id, str) or not _ID.fullmatch(record_id):", "if not isinstance(record_id, str):"),
    ("C04 missing keys accepted", CUR, 'if not isinstance(data, dict) or not {"t", "i"} <= set(data):', 'if not isinstance(data, dict):'),
    ("C09 non-object JSON accepted", CUR, 'if not isinstance(data, dict) or not {"t", "i"} <= set(data):', 'if not {"t", "i"} <= set(data):'),
    ("C10 length guard removed", CUR, "    if not raw or len(raw) > MAX_CURSOR_LENGTH:\n", "    if not raw:\n"),
    ("C05 non-string time accepted", CUR, "    if not isinstance(timestamp, str):\n        raise ValueError(\"bad cursor time\")\n", ""),
    ("C07 non-canonical cursors accepted", CUR, "    if encode_cursor(position) != raw:\n", "    if False:\n"),
    ("C08 encode keeps the original offset", CUR, '{"t": position.created_at.astimezone(UTC).isoformat(),', '{"t": position.created_at.isoformat(),'),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True, timeout=300).returncode
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
