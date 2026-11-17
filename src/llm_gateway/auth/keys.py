"""API keys: generation, parsing and verification.

A key looks like ``llmgw_<prefix>_<secret>``. The prefix (12 hex chars) is public: it
identifies the key in lists and audit logs. Only ``sha256("<prefix>:<secret>")`` is
stored, so a leaked database does not leak usable keys, and binding the prefix into
the hash means a stolen hash cannot be moved onto another key's row.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

KEY_SCHEME = "llmgw"
# token_urlsafe(32) is always exactly 43 base64url characters. Matching the whole key
# rejects whitespace, control characters, non-ASCII and oversize input before hashing.
_KEY_RE = re.compile(rf"{KEY_SCHEME}_([0-9a-f]{{12}})_([A-Za-z0-9_-]{{43}})")


class Role(StrEnum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str


@dataclass(frozen=True)
class ApiKey:
    id: str
    tenant_id: str
    role: Role
    prefix: str
    secret_hash: str
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


def hash_secret(prefix: str, secret: str) -> str:
    # SHA-256 is enough for 256-bit random secrets; slow hashes (bcrypt) are for passwords.
    return hashlib.sha256(f"{prefix}:{secret}".encode()).hexdigest()


def generate_key(tenant_id: str, role: Role, now: datetime) -> tuple[str, ApiKey]:
    """Return ``(full_key, record)``. The full key is shown once and never stored."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    record = ApiKey(
        id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        role=role,
        prefix=prefix,
        secret_hash=hash_secret(prefix, secret),
        created_at=now,
    )
    return f"{KEY_SCHEME}_{prefix}_{secret}", record


def parse_key(raw: str) -> tuple[str, str] | None:
    """Split a full key into ``(prefix, secret)``, or ``None`` if it is malformed."""
    # The prefix has a fixed length, so a "_" inside the base64url secret is unambiguous.
    match = _KEY_RE.fullmatch(raw)
    if match is None:
        return None
    prefix, secret = match.groups()
    return prefix, secret


def verify_secret(key: ApiKey, secret: str) -> bool:
    # Constant-time comparison: response time must not reveal how much of a guess matched.
    return hmac.compare_digest(hash_secret(key.prefix, secret), key.secret_hash)
