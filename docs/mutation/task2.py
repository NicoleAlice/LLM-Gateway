"""Mutation run for LLM-Gateway Task 2. Each mutant: apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
P = ROOT / "src" / "llm_gateway" / "providers"
BASE, FAKE = P / "base.py", P / "fake.py"

MUTANTS = [
    ("T2-01 content stripped", BASE, "    return text\n", "    return text.strip()\n"),
    ("T2-02 blank check removed", BASE, "    if not visible.strip():", "    if False:"),
    ("T2-03 content max_length raised", BASE, "max_length=32_000", "max_length=40_000"),
    ("T2-04 model max_length removed", BASE, "Field(None, min_length=1, max_length=100)", "Field(None, min_length=1)"),
    ("T2-05 messages max_length removed", BASE, "Field(max_length=100)\n", "Field()\n"),
    ("T2-06 user-message check removed", BASE, 'if not any(message.role == "user"', 'if not any(True'),
    ("T2-07 any role accepted as user", BASE, 'message.role == "user"', 'message.role != "x"'),
    ("T2-08 max_tokens ge=0", BASE, "ge=1, le=8192, strict", "ge=0, le=8192, strict"),
    ("T2-09 max_tokens le=8193", BASE, "ge=1, le=8192, strict", "ge=1, le=8193, strict"),
    ("T2-10 default max_tokens changed", BASE, "Field(1024,", "Field(4096,"),
    ("T2-11 usage allows negatives", BASE, "input_tokens: int = Field(ge=0)", "input_tokens: int = Field()"),
    ("T2-11b usage allows negative output", BASE, "output_tokens: int = Field(ge=0)", "output_tokens: int = Field()"),
    ("T2-04b model max_length too strict", BASE, "Field(None, min_length=1, max_length=100)", "Field(None, min_length=1, max_length=99)"),
    ("T2-05b messages max_length too strict", BASE, "Field(max_length=100)\n", "Field(max_length=99)\n"),
    ("T2-12 extra role accepted", BASE, 'Literal["system", "user", "assistant"]', 'Literal["system", "user", "assistant", "tool"]'),
    ("T2-20 empty model accepted", BASE, "Field(None, min_length=1, max_length=100)", "Field(None, max_length=100)"),
    ("T2-21 extra fields allowed", BASE, 'ConfigDict(extra="forbid", frozen=True)', 'ConfigDict(frozen=True)'),
    ("T2-22 requests mutable", BASE, 'ConfigDict(extra="forbid", frozen=True)', 'ConfigDict(extra="forbid")'),
    ("T2-23 lax max_tokens", BASE, "le=8192, strict=True)", "le=8192)"),
    ("T2-24 format chars count as content", BASE, 'if unicodedata.category(c) != "Cf"', 'if True'),
    ("T2-25 fake no-user guard removed", FAKE, "        if last_user is None:", "        if False:"),
    ("T2-13 fake uses first user message", FAKE, "reversed(request.messages)", "request.messages"),
    ("T2-14 fake counts only last message", FAKE, "sum(_words(m.content) for m in request.messages)", "_words(last_user.content)"),
    ("T2-15 output tokens exclude prefix", FAKE, "output_tokens=_words(content)", "output_tokens=_words(last_user.content)"),
    ("T2-16 fail flag ignored", FAKE, "        if self.fail:", "        if False:"),
    ("T2-17 provider name changed", FAKE, 'name = "fake"', 'name = "echo"'),
    ("T2-18 requested model ignored", FAKE, "model=model,", 'model="fake-echo",'),
    ("T2-19 wrong exception type", FAKE, 'raise ProviderError("fake failure")', 'raise RuntimeError("fake failure")'),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
