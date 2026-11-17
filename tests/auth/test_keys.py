import dataclasses
import hashlib
import hmac
from datetime import datetime

import pytest

import llm_gateway.auth.keys as keys_module
from llm_gateway.auth.keys import Role, generate_key, parse_key, verify_secret


def _parse(raw: str) -> tuple[str, str]:
    parsed = parse_key(raw)
    assert parsed is not None
    return parsed


def test_generated_key_verifies(fixed_now: datetime) -> None:
    raw, key = generate_key("t1", Role.DEVELOPER, fixed_now)
    prefix, secret = _parse(raw)
    assert raw.startswith("llmgw_") and prefix == key.prefix and len(prefix) == 12
    assert key.secret_hash == hashlib.sha256(f"{prefix}:{secret}".encode()).hexdigest()
    assert verify_secret(key, secret)
    assert not verify_secret(key, secret + "x")


def test_record_never_holds_the_secret(fixed_now: datetime) -> None:
    raw, key = generate_key("t1", Role.ADMIN, fixed_now)
    _, secret = _parse(raw)
    assert all(secret not in str(v) for v in dataclasses.astuple(key))


def test_verify_uses_constant_time_compare(
    monkeypatch: pytest.MonkeyPatch, fixed_now: datetime
) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(keys_module.hmac, "compare_digest", spy)
    raw, key = generate_key("t1", Role.ADMIN, fixed_now)
    assert verify_secret(key, _parse(raw)[1]) and len(calls) == 1


def test_generated_record_fields(fixed_now: datetime) -> None:
    _, first = generate_key("t1", Role.VIEWER, fixed_now)
    _, second = generate_key("t1", Role.VIEWER, fixed_now)
    assert (first.tenant_id, first.role, first.created_at) == ("t1", Role.VIEWER, fixed_now)
    assert first.revoked_at is None and first.is_active
    assert first.id != second.id and first.prefix != second.prefix


def test_secret_has_at_least_256_bits(fixed_now: datetime) -> None:
    raw, _ = generate_key("t1", Role.ADMIN, fixed_now)
    # token_urlsafe(32): 32 random bytes -> 43 base64url characters.
    assert len(_parse(raw)[1]) >= 43


SECRET = "ab_c-d_e" + "x" * 35  # 43 base64url chars, with "_" and "-" inside
PREFIX = "0a1b2c3d4e5f"


def test_parse_handles_underscores_in_secret() -> None:
    assert parse_key(f"llmgw_{PREFIX}_{SECRET}") == (PREFIX, SECRET)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "llmgw_",
        f"sk_{PREFIX}_{SECRET}",
        f"LLMGW_{PREFIX}_{SECRET}",
        f"llmgw_XYZ_{SECRET}",
        f"llmgw_{PREFIX}_",
        f"llmgw_0a1b2c3d_{SECRET}",
        f"llmgw_0A1B2C3D4E5F_{SECRET}",
        f"llmgw_{PREFIX}_{SECRET[:-1]}",  # 42 chars
        f"llmgw_{PREFIX}_{SECRET}x",  # 44 chars
        f"llmgw_{PREFIX}_{SECRET}\n",
        f" llmgw_{PREFIX}_{SECRET}",
        f"llmgw_{PREFIX}_{SECRET[:-1]} ",
        f"llmgw_{PREFIX}_{SECRET[:-1]}" + chr(0xE9),
        f"llmgw_{PREFIX}_{SECRET[:-1]}" + chr(0xD800),  # lone surrogate: can't be encoded
        f"llmgw_{PREFIX}_{SECRET[:-1]}=",  # base64url never pads
    ],
)
def test_parse_rejects_malformed(raw: str) -> None:
    assert parse_key(raw) is None


def test_every_generated_key_parses(fixed_now: datetime) -> None:
    for _ in range(200):
        raw, key = generate_key("t1", Role.ADMIN, fixed_now)
        assert _parse(raw)[0] == key.prefix
