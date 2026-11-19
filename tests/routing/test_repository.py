import pytest

from llm_gateway.auth.keys import Tenant
from llm_gateway.routing.rules import RoutingRule
from tests.contracts import Repos


async def _tenants(repos: Repos, *ids: str) -> None:
    # PostgreSQL requires the tenant to exist (foreign key); memory doesn't care.
    for tenant_id in ids:
        await repos.tenants.add(Tenant(id=tenant_id, name=f"tenant {tenant_id}"))


async def test_no_rule_is_none(repos: Repos) -> None:
    await _tenants(repos, "t1")
    assert await repos.routing.get("t1") is None


@pytest.mark.parametrize("rule", [RoutingRule("preferred", "a"), RoutingRule("mandatory", "b")])
async def test_set_then_get_round_trips(repos: Repos, rule: RoutingRule) -> None:
    await _tenants(repos, "t1")
    await repos.routing.set("t1", rule)
    assert await repos.routing.get("t1") == rule


async def test_set_replaces_the_rule(repos: Repos) -> None:
    await _tenants(repos, "t1")
    await repos.routing.set("t1", RoutingRule("preferred", "fake-echo"))
    await repos.routing.set("t1", RoutingRule("mandatory", "fake-big"))
    assert await repos.routing.get("t1") == RoutingRule("mandatory", "fake-big")


async def test_rules_are_per_tenant(repos: Repos) -> None:
    await _tenants(repos, "t1", "t2")
    await repos.routing.set("t1", RoutingRule("mandatory", "fake-big"))
    await repos.routing.set("t2", RoutingRule("preferred", "fake-echo"))
    assert await repos.routing.get("t1") == RoutingRule("mandatory", "fake-big")
    assert await repos.routing.get("t2") == RoutingRule("preferred", "fake-echo")
    await repos.routing.delete("t2")
    assert await repos.routing.get("t1") == RoutingRule("mandatory", "fake-big")
    assert await repos.routing.get("t2") is None


async def test_delete_is_idempotent(repos: Repos) -> None:
    await _tenants(repos, "t1", "t2")
    await repos.routing.delete("t2")  # nothing there: a no-op
    await repos.routing.set("t1", RoutingRule("preferred", "a"))
    await repos.routing.delete("t1")
    await repos.routing.delete("t1")
    assert await repos.routing.get("t1") is None


async def test_a_rule_for_an_unknown_tenant_is_refused_on_postgres(repos: Repos) -> None:
    if repos.backend != "postgres":
        pytest.skip("only PostgreSQL knows which tenants exist")
    with pytest.raises(ValueError, match="unknown tenant") as excinfo:
        await repos.routing.set("ghost", RoutingRule("preferred", "a"))
    assert excinfo.value.__context__ is None and excinfo.value.__cause__ is None
    assert await repos.routing.get("ghost") is None
