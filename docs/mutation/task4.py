"""Mutation run for LLM-Gateway Task 4. Each mutant: apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
ERR, DEPS, ADM = S / "api" / "errors.py", S / "auth" / "deps.py", S / "api" / "admin.py"

MUTANTS = [
    ("E01 validation message leaks input", ERR, 'return error_response(422, "invalid_request", "invalid fields: " + ", ".join(fields))', 'return error_response(422, "invalid_request", str(exc))'),
    ("E02 validation code wrong", ERR, '422, "invalid_request"', '422, "bad_request"'),
    ("E03 http handler removed", ERR, "    @app.exception_handler(StarletteHTTPException)\n", "    # removed\n"),
    ("E04 405 code wrong", ERR, '405: "method_not_allowed"', '405: "not_found"'),
    ("E05 500 leaks exception text", ERR, 'error_response(500, "internal_error", "internal server error")', 'error_response(500, "internal_error", str(exc))'),
    ("E06 500 handler removed", ERR, "    @app.exception_handler(Exception)\n", "    # removed\n"),
    ("E07 ApiError headers dropped", ERR, "return error_response(exc.status_code, exc.code, exc.message, exc.headers)", "return error_response(exc.status_code, exc.code, exc.message)"),
    ("D01 scheme not checked", DEPS, '    if scheme.lower() != "bearer":\n        raise _invalid_key()\n', ""),
    ("D02 scheme case-sensitive", DEPS, 'scheme.lower() != "bearer"', 'scheme != "Bearer"'),
    ("D03 secret not verified", DEPS, " or not verify_secret(key, secret)", ""),
    ("D04 revoked keys accepted", DEPS, " or not key.is_active", ""),
    ("D05 role check disabled", DEPS, "        if principal.role not in allowed:\n", "        if False:\n"),
    ("D06 every role allowed", DEPS, "allowed = frozenset(roles)", "allowed = frozenset(Role)"),
    ("D08 admin token prefix match", DEPS, "not hmac.compare_digest(given, expected)", "not expected.startswith(given)"),
    ("D09 admin token compared as str", DEPS, "given = (x_admin_token or \"\").encode()", "given = (x_admin_token or \"\")  # type: ignore"),
    ("D10 principal tenant wrong", DEPS, "Principal(tenant_id=key.tenant_id,", 'Principal(tenant_id="t",'),
    ("A01 no retry", ADM, "MAX_KEY_ATTEMPTS = 3", "MAX_KEY_ATTEMPTS = 1"),
    ("A02 too many retries", ADM, "MAX_KEY_ATTEMPTS = 3", "MAX_KEY_ATTEMPTS = 10"),
    ("A03 collision not caught", ADM, "        except DuplicateKeyPrefixError:\n            continue\n", "        except KeyError:\n            continue\n"),
    ("A04 requested role ignored", ADM, "await _issue_key(state, admin.tenant_id, body.role)", "await _issue_key(state, admin.tenant_id, Role.VIEWER)"),
    ("A05 key issued in wrong tenant", ADM, "await _issue_key(state, admin.tenant_id, body.role)", 'await _issue_key(state, "other", body.role)'),
    ("A06 list uses principal key id as tenant", ADM, "return [KeyInfo.of(key) for key in await state.keys.list_for_tenant(admin.tenant_id)]", "return [KeyInfo.of(key) for t in {admin.tenant_id, *getattr(state.keys, '_by_prefix', {}) and {k.tenant_id for k in state.keys._by_prefix.values()}} for key in await state.keys.list_for_tenant(t)]  # type: ignore"),
    ("A08 404 check removed", ADM, "    if target is None:\n        raise ApiError(404,", "    if False:\n        raise ApiError(404,"),
    ("A09 revoked admins count", ADM, "if k.role is Role.ADMIN and k.is_active and k.id != key_id]", "if k.role is Role.ADMIN and k.id != key_id]"),
    ("A10 last-admin check removed", ADM, "        if not others:\n", "        if False:\n"),
    ("A13 revoke not called", ADM, "    await state.keys.revoke(admin.tenant_id, key_id, state.clock())\n", ""),
    ("A15 tenant name unbounded", ADM, "name: str = Field(min_length=1, max_length=100)", "name: str"),
    ("A16 tenant body allows extra", ADM, 'class TenantCreate(BaseModel):\n    model_config = ConfigDict(extra="forbid")', "class TenantCreate(BaseModel):\n    pass"),
    ("A17 key body allows extra", ADM, 'class KeyCreate(BaseModel):\n    model_config = ConfigDict(extra="forbid")', "class KeyCreate(BaseModel):\n    pass"),
    ("A18 bootstrap key not admin", ADM, "await _issue_key(state, tenant.id, Role.ADMIN)", "await _issue_key(state, tenant.id, Role.DEVELOPER)"),
    ("A19 create returns 200", ADM, '@router.post("/keys", status_code=201)', '@router.post("/keys")'),
    ("A20 delete returns 200", ADM, "return Response(status_code=204)", "return Response(status_code=200)"),
    ("A21 tenant not stored", ADM, "    await state.tenants.add(tenant)\n", ""),
    ("A22 tenant stored before key", ADM, "    raw, _ = await _issue_key(state, tenant.id, Role.ADMIN)\n    await state.tenants.add(tenant)\n", "    await state.tenants.add(tenant)\n    raw, _ = await _issue_key(state, tenant.id, Role.ADMIN)\n"),
    ("E08 extra field name echoed", ERR, '    if error["type"] == "extra_forbidden":\n', "    if False:\n"),
    ("E09 nonstandard status crashes", ERR, "        return HTTPStatus(status_code).phrase\n    except ValueError:", "        return HTTPStatus(status_code).phrase\n    except KeyError:"),
    ("E10 413 code wrong", ERR, '413: "payload_too_large"', '413: "http_error"'),
    ("L01 middleware not installed", S / "main.py", "    app.add_middleware(BodySizeLimit, max_bytes=state.settings.max_body_bytes)\n", ""),
    ("L02 content-length fast path off", S / "api" / "limits.py", "int(declared) > self.max_bytes", "False"),
    ("L03 streamed bytes not counted", S / "api" / "limits.py", "                if received > self.max_bytes:\n", "                if False:\n"),
    ("L04 off-by-one at the limit", S / "api" / "limits.py", "int(declared) > self.max_bytes", "int(declared) >= self.max_bytes"),
    ("L05 limit setting ignored", S / "main.py", "max_bytes=state.settings.max_body_bytes", "max_bytes=10_000_000"),
    ("L06 default limit changed", S / "config.py", "Field(1_048_576, ge=1024)", "Field(10_485_760, ge=1024)"),
]

CMD = ["uv", "run", "pytest", "-q", "-x", "-p", "no:warnings"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
