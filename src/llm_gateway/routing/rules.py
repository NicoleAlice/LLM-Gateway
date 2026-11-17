"""Which model answers a request: the tenant's rule, the request, or the default."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

RoutingMode = Literal["preferred", "mandatory"]


@dataclass(frozen=True)
class RoutingRule:
    """``preferred``: used when the request names no model. ``mandatory``: always used."""

    mode: RoutingMode
    model: str


class UnknownModelError(Exception):
    """The chosen model is not in the catalog.

    ``model`` is the model routing chose (what an audit row must record), and
    ``from_rule`` says whether the tenant's rule chose it: then the fix is the tenant's
    configuration, not the caller's request.
    """

    def __init__(self, model: str, *, from_rule: bool) -> None:
        super().__init__(model)
        self.model = model
        self.from_rule = from_rule


def resolve_model(
    rule: RoutingRule | None,
    requested: str | None,
    default_model: str,
    catalog: Mapping[str, str],
) -> tuple[str, str]:
    """Return ``(provider_name, model)``.

    Precedence: mandatory rule > requested model > preferred rule > default. A mandatory
    rule wins even over an unknown requested model, because the request is overridden.
    """
    from_rule = True
    if rule is not None and rule.mode == "mandatory":
        model = rule.model
    elif requested is not None:
        model, from_rule = requested, False
    elif rule is not None:
        model = rule.model
    else:
        model, from_rule = default_model, False
    provider = catalog.get(model)
    if provider is None:
        raise UnknownModelError(model, from_rule=from_rule)
    return provider, model
