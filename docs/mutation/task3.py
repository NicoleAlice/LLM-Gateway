"""Mutation run for LLM-Gateway Task 3 (after review fixes). Apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
A = ROOT / "src" / "llm_gateway" / "auth"
KEYS, REPO = A / "keys.py", A / "repository.py"
RE_LINE = '_KEY_RE = re.compile(rf"{KEY_SCHEME}_([0-9a-f]{{12}})_([A-Za-z0-9_-]{{43}})")'

MUTANTS = [
    ("K01 hash not bound to prefix", KEYS, 'f"{prefix}:{secret}".encode()', "secret.encode()"),
    ("K02 weaker hash", KEYS, "hashlib.sha256(", "hashlib.md5("),
    ("K03 plain == compare", KEYS, "hmac.compare_digest(hash_secret(key.prefix, secret), key.secret_hash)", "hash_secret(key.prefix, secret) == key.secret_hash"),
    ("K04 match instead of fullmatch", KEYS, "_KEY_RE.fullmatch(raw)", "_KEY_RE.match(raw)"),
    ("K05 scheme not checked", KEYS, RE_LINE, RE_LINE.replace("{KEY_SCHEME}_", r"\w+_")),
    ("K06 prefix any length", KEYS, RE_LINE, RE_LINE.replace("{{12}}", "+")),
    ("K07 uppercase hex accepted", KEYS, RE_LINE, RE_LINE.replace("[0-9a-f]", "[0-9a-fA-F]")),
    ("K08 prefix 8 hex", KEYS, "secrets.token_hex(6)", "secrets.token_hex(4)"),
    ("K09 secret any length", KEYS, RE_LINE, RE_LINE.replace("{{43}}", "+")),
    ("K09b secret 1..43", KEYS, RE_LINE, RE_LINE.replace("{{43}}", "{{1,43}}")),
    ("K09c secret 43..", KEYS, RE_LINE, RE_LINE.replace("{{43}}", "{{43,}}")),
    ("K09d secret any charset", KEYS, RE_LINE, RE_LINE.replace("[A-Za-z0-9_-]", "."),),
    ("K09e padding allowed", KEYS, RE_LINE, RE_LINE.replace("[A-Za-z0-9_-]", "[A-Za-z0-9_=-]")),
    ("K10 is_active inverted", KEYS, "return self.revoked_at is None", "return self.revoked_at is not None"),
    ("K11 short secret", KEYS, "secrets.token_urlsafe(32)", "secrets.token_urlsafe(8)"),
    ("K12 role ignored", KEYS, "        role=role,\n", "        role=Role.ADMIN,\n"),
    ("K13 tenant ignored", KEYS, "        tenant_id=tenant_id,\n", '        tenant_id="t",\n'),
    ("K14 created_at ignored", KEYS, "        created_at=now,\n", "        created_at=datetime.min,\n"),
    ("K15 key id constant", KEYS, "id=uuid.uuid4().hex", 'id="key"'),
    ("K16 secret not in returned key", KEYS, 'f"{KEY_SCHEME}_{prefix}_{secret}"', 'f"{KEY_SCHEME}_{prefix}_x"'),
    ("R01 duplicate prefix overwrites", REPO, "        if key.prefix in self._by_prefix:\n            raise DuplicateKeyPrefixError(key.prefix)\n", ""),
    ("R01b duplicate id accepted", REPO, "        if key.id in self._prefix_by_id:\n", "        if False:\n"),
    ("R02 revoke ignores tenant", REPO, "if key is None or key.tenant_id != tenant_id:", "if key is None:"),
    ("R03 revoke not idempotent", REPO, "        if key.revoked_at is not None:\n            return RevokeOutcome.ALREADY_REVOKED, key\n", ""),
    ("R04 revoke not persisted", REPO, "        self._by_prefix[key.prefix] = revoked\n", ""),
    ("R05 list not filtered by tenant", REPO, "if k.tenant_id == tenant_id]", "]"),
    ("R06 list newest first", REPO, "key=lambda k: (k.created_at, k.id))", "key=lambda k: (k.created_at, k.id), reverse=True)"),
    ("R06b no id tie-break", REPO, "key=lambda k: (k.created_at, k.id))", "key=lambda k: k.created_at)"),
    ("R07 tenant get broken", REPO, "return self._tenants.get(tenant_id)", "return None"),
    ("R08 tenant add drops", REPO, "        self._tenants[tenant.id] = tenant\n", "        pass\n"),
    ("R08b duplicate tenant overwrites", REPO, "        if tenant.id in self._tenants:\n", "        if False:\n"),
    ("R09 unknown id crashes", REPO, "prefix = self._prefix_by_id.get(key_id)", "prefix = self._prefix_by_id[key_id]"),
    ("R10 get_by_prefix broken", REPO, "return self._by_prefix.get(prefix)\n", "return None\n"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op mutant"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
