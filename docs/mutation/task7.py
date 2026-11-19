"""Mutation run for LLM-Gateway Task 7 (after review fixes). Apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
CHAT, AUD, STATE = S / "api" / "chat.py", S / "audit" / "records.py", S / "state.py"

PRICE_THEN_CALL = '    try:\n        price = state.prices.price_for(model, today)\n    except NoPriceError:\n        raise ApiError(503, "pricing_unavailable", "this model has no price today") from None\n\n    outcome.provider = provider_name\n    timeout_s = state.settings.provider_timeout_s\n    answered = False\n    error: type[Exception] | None = None  # the type only: its text may echo the prompt\n    try:\n        # Our own deadline, told apart from a TimeoutError the provider raises itself.\n        with anyio.move_on_after(timeout_s) as deadline:\n            raw = await provider.chat(body, model)\n'
CALL_THEN_PRICE = '    outcome.provider = provider_name\n    timeout_s = state.settings.provider_timeout_s\n    answered = False\n    error: type[Exception] | None = None  # the type only: its text may echo the prompt\n    try:\n        # Our own deadline, told apart from a TimeoutError the provider raises itself.\n        with anyio.move_on_after(timeout_s) as deadline:\n            raw = await provider.chat(body, model)\n            try:\n                price = state.prices.price_for(model, today)\n            except NoPriceError:\n                raise ApiError(503, "pricing_unavailable", "this model has no price today") from None\n'
SUCCESS_TAIL = (
    "    outcome.usage, outcome.cost_usd = answer.usage, amount\n"
    '    outcome.status, outcome.error_code = "ok", None\n    return result\n'
)

MUTANTS = [
    ("C01 any role can chat", CHAT, "Depends(require_role(Role.DEVELOPER))", "Depends(require_role(*Role))"),
    ("C02 provider called before pricing", CHAT, PRICE_THEN_CALL, CALL_THEN_PRICE),
    ("C03 no audit on success", CHAT, "            await state.audit.add(record)\n", '            if record.status != "ok":\n                await state.audit.add(record)\n'),
    ("C04 no audit on error", CHAT, "            await state.audit.add(record)\n", '            if record.status == "ok":\n                await state.audit.add(record)\n'),
    ("C05 errors audited as ok", CHAT, '    status: AuditStatus = "error"\n', '    status: AuditStatus = "ok"\n'),
    ("C06 failed calls billed", CHAT, "    cost_usd: Decimal = ZERO_COST\n", '    cost_usd: Decimal = Decimal("0.000001")\n'),
    ("C07 cancel not coded", CHAT, '        return "cancelled"\n', '        return "internal_error"\n'),
    ("C08 ApiError code ignored", CHAT, "        return exc.code\n", '        return "internal_error"\n'),
    ('C09 only ProviderError mapped', CHAT, '    except Exception as exc:\n        error = type(exc)\n', '    except LookupError as exc:\n        error = type(exc)\n'),
    ("C10 stale rule reported as unknown_model", CHAT, "        if exc.from_rule:\n", "        if False:\n"),
    ("C11 audit keeps requested model", CHAT, "        outcome.model = exc.model\n", "        pass\n"),
    ("C12 missing provider is KeyError", CHAT, "    provider = state.providers.get(provider_name)\n", "    provider = state.providers[provider_name]\n"),
    ("C13 clock read again for audit", CHAT, "            created_at=now,\n", "            created_at=state.clock(),\n"),
    ("C14 shield removed", CHAT, "anyio.move_on_after(AUDIT_WRITE_TIMEOUT_S, shield=True)", "anyio.move_on_after(AUDIT_WRITE_TIMEOUT_S, shield=False)"),
    ("C15 latency not measured", CHAT, "latency_ms=int((perf_counter() - started) * 1000)", "latency_ms=0"),
    ("C16 cost rendered via float", CHAT, "cost_usd=str(amount),", "cost_usd=str(float(amount)),"),
    ("C17 usage not recorded", CHAT, "    outcome.usage, outcome.cost_usd = answer.usage, amount\n", "    outcome.cost_usd = amount\n"),
    ("C18 audit tenant wrong", CHAT, "            tenant_id=caller.tenant_id,\n", '            tenant_id="t",\n'),
    ("C19 audit key id wrong", CHAT, "            key_id=caller.key_id,\n", "            key_id=caller.key_prefix,\n"),
    ("C20 provider not recorded before call", CHAT, "    outcome.provider = provider_name\n", ""),
    ("C21 routing rule ignored", CHAT, "    rule = await state.routing.get(caller.tenant_id)\n", "    rule = None\n"),
    ("C22 price date from wall clock", CHAT, "        return await _answer(body, state, caller, now.date(), outcome)", "        return await _answer(body, state, caller, __import__('datetime').date.today(), outcome)"),
    ("C23 cost uses wrong usage", CHAT, "    amount = cost(answer.usage, price)\n", "    amount = cost(Usage(input_tokens=1, output_tokens=1), price)\n"),
    ("N01 billed before the response is built", CHAT, "    result = ChatResult(", "    outcome.usage, outcome.cost_usd = answer.usage, amount\n    outcome.status, outcome.error_code = \"ok\", None\n    result = ChatResult("),
    ("N02 default error code None", CHAT, '    error_code: str | None = "internal_error"\n', "    error_code: str | None = None\n"),
    ("N03 audit errors not caught", CHAT, "    except Exception:\n        logger.exception(\"audit write failed: %s\", _reconcile(record))\n        return False\n", "    except LookupError:\n        return False\n"),
    ("N04 no fail-closed on answered calls", CHAT, "        if not written and failure is None:\n", "        if False:\n"),
    ("N05 fail-closed masks original errors", CHAT, "        if not written and failure is None:\n", "        if not written:\n"),
    ("N06 timeout not detected", CHAT, "    if scope.cancelled_caught:\n", "    if False:\n"),
    ("N07 provider response not revalidated", CHAT, "        answer = ChatResponse.model_validate(raw.model_dump())\n", "        answer = raw\n"),
    ('N08 provider log loses the type name', CHAT, '"provider %s failed for model %s: %s", provider_name, model, error.__name__', '"provider %s failed for model %s: %s", provider_name, model, error'),
    ('N09 provider failure not logged', CHAT, '    if error is not None:\n        logger.warning("provider %s failed', '    if error is not None:\n        (lambda *a: None)("provider %s failed'),
    ("N10 reconcile line omits cost", CHAT, ' cost={record.cost_usd}"', '"'),
    ("N11 unanswered call handed out on audit failure", CHAT, '            raise ApiError(500, "audit_unavailable", "the request could not be recorded")\n', "            pass\n"),
    ("U01 audit window closed", AUD, "start <= r.created_at < end", "start <= r.created_at <= end"),
    ("U02 audit window drops start", AUD, "start <= r.created_at < end", "start < r.created_at < end"),
    ("U03 audit not per tenant", AUD, "r.tenant_id == tenant_id and ", ""),
    ("U04 audit newest first", AUD, "sorted(rows, key=position_of)", "sorted(rows, key=position_of, reverse=True)"),
    ("S01 default provider missing", STATE, 'return {"fake": FakeProvider()}', "return {}"),
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
