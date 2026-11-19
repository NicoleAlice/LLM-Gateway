"""Mutation run for LLM-Gateway Task 5. Each mutant: apply, run pytest, restore."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
RULES, REPO, ADM, STATE = S / "routing" / "rules.py", S / "routing" / "repository.py", S / "api" / "admin.py", S / "state.py"

MUTANTS = [
    ("R01 mandatory treated as preferred", RULES, 'rule.mode == "mandatory"', 'rule.mode == "never"'),
    ("R02 preferred beats request", RULES, "    elif requested is not None:\n        model, from_rule = requested, False\n    elif rule is not None:\n        model = rule.model\n", "    elif rule is not None:\n        model = rule.model\n    elif requested is not None:\n        model, from_rule = requested, False\n"),
    ("R03 preferred rule ignored", RULES, "    elif rule is not None:\n        model = rule.model\n    else:", "    else:"),
    ("R04 default ignored", RULES, "        model, from_rule = default_model, False\n", '        model, from_rule = "fake-echo", False\n'),
    ("R05 catalog not checked", RULES, "    if provider is None:\n        raise UnknownModelError(model, from_rule=from_rule)\n", "    if provider is None:\n        provider = \"fake\"\n"),
    ("R06 provider hard-coded", RULES, "    return provider, model", '    return "fake", model'),
    ("R07 error reports requested not chosen", RULES, "raise UnknownModelError(model, from_rule=from_rule)", "raise UnknownModelError(requested or default_model, from_rule=from_rule)"),
    ("R08 from_rule always False", RULES, "raise UnknownModelError(model, from_rule=from_rule)", "raise UnknownModelError(model, from_rule=False)"),
    ("R09 from_rule always True", RULES, "raise UnknownModelError(model, from_rule=from_rule)", "raise UnknownModelError(model, from_rule=True)"),
    ("P05 set is global last-write-wins", REPO, "        self._rules[tenant_id] = rule\n", "        self._rules.clear()\n        self._rules[tenant_id] = rule\n"),
    ("P01 repo not per tenant", REPO, "return self._rules.get(tenant_id)", "return next(iter(self._rules.values()), None)"),
    ("P02 set keeps first rule", REPO, "        self._rules[tenant_id] = rule\n", "        self._rules.setdefault(tenant_id, rule)\n"),
    ("P03 delete does nothing", REPO, "        self._rules.pop(tenant_id, None)\n", "        pass\n"),
    ("P04 delete missing raises", REPO, "self._rules.pop(tenant_id, None)", "self._rules.pop(tenant_id)"),
    ("A01 put skips catalog check", ADM, "    if body.model not in state.catalog:\n", "    if False:\n"),
    ("A02 put stores before check", ADM, '    if body.model not in state.catalog:\n        raise ApiError(400, "unknown_model", "model is not in the catalog")\n    await state.routing.set(admin.tenant_id, RoutingRule(mode=body.mode, model=body.model))\n', '    await state.routing.set(admin.tenant_id, RoutingRule(mode=body.mode, model=body.model))\n    if body.model not in state.catalog:\n        raise ApiError(400, "unknown_model", "model is not in the catalog")\n'),
    ("A03 get 404 code wrong", ADM, '"no_routing_rule"', '"not_found"'),
    ("A04 routing open to all roles", ADM, "async def get_routing_rule(state: State, admin: Admin)", "async def get_routing_rule(state: State, admin: Annotated[Principal, Depends(require_role(*Role))])"),
    ("A05 put open to all roles", ADM, "async def set_routing_rule(body: RoutingRuleBody, state: State, admin: Admin)", "async def set_routing_rule(body: RoutingRuleBody, state: State, admin: Annotated[Principal, Depends(require_role(*Role))])"),
    ("A06 delete open to all roles", ADM, "async def delete_routing_rule(state: State, admin: Admin)", "async def delete_routing_rule(state: State, admin: Annotated[Principal, Depends(require_role(*Role))])"),
    ("A07 rule body allows extra", ADM, 'class RoutingRuleBody(BaseModel):\n    model_config = ConfigDict(extra="forbid")', "class RoutingRuleBody(BaseModel):\n    pass"),
    ("A08 empty model allowed", ADM, "    model: str = Field(min_length=1, max_length=100)\n\n\nclass KeyCreated", "    model: str = Field(max_length=100)\n\n\nclass KeyCreated"),
    ("A09 put stores wrong mode", ADM, "RoutingRule(mode=body.mode, model=body.model)", 'RoutingRule(mode="preferred", model=body.model)'),
    ("A10 delete wrong tenant", ADM, "    await state.routing.delete(admin.tenant_id)\n", '    await state.routing.delete("x")\n'),
    ("C01 default catalog empty", STATE, 'return {"fake-echo": "fake"}', "return {}"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"]
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
