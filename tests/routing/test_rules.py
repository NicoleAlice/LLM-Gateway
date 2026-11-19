import pytest

from llm_gateway.routing.rules import RoutingRule, UnknownModelError, resolve_model

CAT = {"fake-echo": "fake", "fake-big": "fake"}


@pytest.mark.parametrize(
    ("rule", "requested", "default", "expected"),
    [
        (None, None, "fake-echo", ("fake", "fake-echo")),
        (None, None, "fake-big", ("fake", "fake-big")),
        (None, "fake-big", "fake-echo", ("fake", "fake-big")),
        (RoutingRule("preferred", "fake-big"), None, "fake-echo", ("fake", "fake-big")),
        (RoutingRule("preferred", "fake-big"), "fake-echo", "fake-echo", ("fake", "fake-echo")),
        (RoutingRule("mandatory", "fake-big"), "fake-echo", "fake-echo", ("fake", "fake-big")),
        (RoutingRule("mandatory", "fake-big"), "gpt-nope", "fake-echo", ("fake", "fake-big")),
    ],
)
def test_resolve_model(
    rule: RoutingRule | None, requested: str | None, default: str, expected: tuple[str, str]
) -> None:
    assert resolve_model(rule, requested, default, CAT) == expected


@pytest.mark.parametrize(
    ("rule", "requested", "default"),
    [
        (None, "gpt-nope", "fake-echo"),
        (RoutingRule("mandatory", "gone"), None, "fake-echo"),
        (RoutingRule("preferred", "gone"), None, "fake-echo"),
        (None, None, "missing-default"),
    ],
)
def test_unknown_model(rule: RoutingRule | None, requested: str | None, default: str) -> None:
    with pytest.raises(UnknownModelError):
        resolve_model(rule, requested, default, CAT)


@pytest.mark.parametrize(
    ("rule", "requested", "default", "model", "from_rule"),
    [
        (None, "gpt-nope", "fake-echo", "gpt-nope", False),
        (None, None, "missing-default", "missing-default", False),
        (RoutingRule("mandatory", "gone"), "fake-echo", "fake-echo", "gone", True),
        (RoutingRule("preferred", "gone"), None, "fake-echo", "gone", True),
    ],
)
def test_unknown_model_reports_what_routing_chose(
    rule: RoutingRule | None, requested: str | None, default: str, model: str, from_rule: bool
) -> None:
    with pytest.raises(UnknownModelError) as excinfo:
        resolve_model(rule, requested, default, CAT)
    assert (excinfo.value.model, excinfo.value.from_rule) == (model, from_rule)


def test_provider_comes_from_catalog() -> None:
    catalog = {"claude-x": "anthropic", "fake-echo": "fake"}
    assert resolve_model(None, "claude-x", "fake-echo", catalog) == ("anthropic", "claude-x")
