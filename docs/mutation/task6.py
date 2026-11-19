"""Mutation run for LLM-Gateway Task 6. Each mutant: apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
P, STATE = S / "billing" / "pricing.py", S / "state.py"

MUTANTS = [
    ("B01 earliest price chosen", P, "return history[index - 1]", "return history[0]"),
    ("B02 bisect_left (strict <)", P, "bisect.bisect_right([p.effective_from for p in history], on)", "bisect.bisect_left([p.effective_from for p in history], on)"),
    ("B03 no-price check removed", P, "        if index == 0:\n            raise NoPriceError", "        if False:\n            raise NoPriceError"),
    ("B04 model ignored", P, "history = self._by_model.get(model, [])", "history = next(iter(self._by_model.values()), [])"),
    ("B05 unsorted insert", P, "history.insert(bisect.bisect(dates, price.effective_from), price)", "history.append(price)"),
    ("B06 duplicate date allowed", P, "        if price.effective_from in dates:\n", "        if False:\n"),
    ("B07 negative allowed", P, "        if self.input_per_mtok < 0 or self.output_per_mtok < 0:\n", "        if False:\n"),
    ("B08 negative output allowed", P, "self.input_per_mtok < 0 or self.output_per_mtok < 0", "self.input_per_mtok < 0"),
    ("B09 no rounding", P, "    return raw.quantize(MICRO_USD, rounding=ROUND_HALF_UP)", "    return raw"),
    ("B10 half-even rounding", P, "rounding=ROUND_HALF_UP", "rounding='ROUND_HALF_EVEN'"),
    ("B11 5 decimals", P, 'MICRO_USD = Decimal("0.000001")', 'MICRO_USD = Decimal("0.00001")'),
    ("B12 output billed at input rate", P, "usage.output_tokens * price.output_per_mtok", "usage.output_tokens * price.input_per_mtok"),
    ("B13 per thousand", P, "_PER_MILLION = Decimal(1_000_000)", "_PER_MILLION = Decimal(1_000)"),
    ("B14 float math", P, "    return raw.quantize(MICRO_USD, rounding=ROUND_HALF_UP)", "    return Decimal(float(raw)).quantize(MICRO_USD, rounding=ROUND_HALF_UP)"),
    ("B15 add does nothing", P, "        history.insert(bisect.bisect(dates, price.effective_from), price)\n", "        pass\n"),
    ("S01 default price wrong", STATE, 'Decimal("1.00"), Decimal("2.00")', 'Decimal("2.00"), Decimal("1.00")'),
    ("S02 default price date wrong", STATE, "date(2026, 1, 1), Decimal", "date(2025, 1, 1), Decimal"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
    finally:
        path.write_text(original, encoding="utf-8", newline="\n")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
