"""Mutation run for plan 1b-A task A3 (atomic revoke with the last-admin rule).

Replaces Task 4's A08-A10 and A13 mutants, whose target (the check inside the DELETE
route) moved into the repository. Apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
REPO, ADM = S / "auth" / "repository.py", S / "api" / "admin.py"

MUTANTS = [
    ("L01 last-admin rule removed", REPO, "        if keep_one_admin and key.role is Role.ADMIN:\n", "        if False:\n"),
    ("L02 revoked admins counted", REPO, "                and k.role is Role.ADMIN\n                and k.is_active\n", "                and k.role is Role.ADMIN\n"),
    ("L03 other tenants' admins counted", REPO, "                if k.tenant_id == tenant_id\n                and k.role is Role.ADMIN\n", "                if k.role is Role.ADMIN\n"),
    ("L04 non-admin keys held back", REPO, "        if keep_one_admin and key.role is Role.ADMIN:\n", "        if keep_one_admin:\n"),
    ("L05 target counts as another admin", REPO, "                and k.is_active\n                and k.id != key.id\n", "                and k.is_active\n"),
    ("L06 rule can't be turned off", REPO, "        if keep_one_admin and key.role is Role.ADMIN:\n", "        if key.role is Role.ADMIN:\n"),
    ("L07 refused revoke still written", REPO, "            if not others:\n                return RevokeOutcome.LAST_ADMIN, key\n", "            if not others:\n                self._by_prefix[key.prefix] = dataclasses.replace(key, revoked_at=now)\n                return RevokeOutcome.LAST_ADMIN, key\n"),
    ("L08 already revoked reported as revoked", REPO, "            return RevokeOutcome.ALREADY_REVOKED, key\n", "            return RevokeOutcome.REVOKED, key\n"),
    ("L09 not-found reported as revoked", REPO, "            return RevokeOutcome.NOT_FOUND, None\n", "            return RevokeOutcome.REVOKED, None\n"),
    ("M01 404 not mapped", ADM, "    if outcome is RevokeOutcome.NOT_FOUND:\n", "    if False:\n"),
    ("M02 409 not mapped", ADM, "    if outcome is RevokeOutcome.LAST_ADMIN:\n", "    if False:\n"),
    ("M03 route turns the rule off", ADM, "admin.tenant_id, key_id, state.clock(), keep_one_admin=True", "admin.tenant_id, key_id, state.clock(), keep_one_admin=False"),
    ("M04 route revokes in the wrong tenant", ADM, "admin.tenant_id, key_id, state.clock(), keep_one_admin=True", '"other", key_id, state.clock(), keep_one_admin=True'),
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
