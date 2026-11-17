from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from llm_gateway.billing.pricing import NoPriceError, Price, PriceTable, cost
from llm_gateway.providers.base import Usage

TABLE = PriceTable(
    [
        Price("m", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00")),
        Price("m", date(2026, 11, 15), Decimal("3.00"), Decimal("6.00")),
    ]
)


def test_price_follows_effective_date() -> None:
    assert TABLE.price_for("m", date(2026, 11, 14)).input_per_mtok == Decimal("1.00")
    assert TABLE.price_for("m", date(2026, 11, 15)).input_per_mtok == Decimal("3.00")
    assert TABLE.price_for("m", date(2026, 12, 1)).input_per_mtok == Decimal("3.00")


def test_no_price_before_first_date_or_unknown_model() -> None:
    with pytest.raises(NoPriceError):
        TABLE.price_for("m", date(2025, 12, 31))
    with pytest.raises(NoPriceError):
        TABLE.price_for("other", date(2026, 11, 17))


def test_cost_uses_both_rates() -> None:
    price = TABLE.price_for("m", date(2026, 1, 1))
    assert cost(Usage(input_tokens=3, output_tokens=5), price) == Decimal("0.000013")
    assert str(cost(Usage(input_tokens=1_000_000, output_tokens=0), price)) == "1.000000"


def test_cost_rounds_half_up_to_micro_usd() -> None:
    p = Price("m", date(2026, 1, 1), Decimal("0.50"), Decimal("2.00"))
    assert cost(Usage(input_tokens=1, output_tokens=1), p) == Decimal("0.000003")
    p2 = Price("m", date(2026, 1, 1), Decimal("0.4"), Decimal("0"))
    assert str(cost(Usage(input_tokens=1, output_tokens=0), p2)) == "0.000000"


def test_insertion_order_does_not_matter() -> None:
    newer = Price("m", date(2026, 11, 15), Decimal("3.00"), Decimal("6.00"))
    older = Price("m", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00"))
    table = PriceTable([newer, older])
    assert table.price_for("m", date(2026, 6, 1)) == older
    assert table.price_for("m", date(2026, 11, 20)) == newer


def test_add_after_construction() -> None:
    table = PriceTable([Price("m", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00"))])
    later = Price("m", date(2026, 11, 18), Decimal("3.00"), Decimal("6.00"))
    table.add(later)
    assert table.price_for("m", date(2026, 11, 18)) == later
    assert table.price_for("m", date(2026, 11, 17)).input_per_mtok == Decimal("1.00")


def test_models_are_priced_independently() -> None:
    table = PriceTable(
        [
            Price("a", date(2026, 1, 1), Decimal("1.00"), Decimal("1.00")),
            Price("b", date(2026, 6, 1), Decimal("9.00"), Decimal("9.00")),
        ]
    )
    assert table.price_for("a", date(2026, 7, 1)).input_per_mtok == Decimal("1.00")
    with pytest.raises(NoPriceError):
        table.price_for("b", date(2026, 5, 31))


def test_duplicate_effective_date_is_rejected() -> None:
    table = PriceTable([Price("m", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00"))])
    with pytest.raises(ValueError, match="already has a price"):
        table.add(Price("m", date(2026, 1, 1), Decimal("5.00"), Decimal("5.00")))
    assert table.price_for("m", date(2026, 1, 1)).input_per_mtok == Decimal("1.00")


@pytest.mark.parametrize(
    ("inp", "out"), [(Decimal("-0.01"), Decimal("1")), (Decimal("1"), Decimal("-1"))]
)
def test_negative_prices_are_rejected(inp: Decimal, out: Decimal) -> None:
    with pytest.raises(ValueError, match="negative"):
        Price("m", date(2026, 1, 1), inp, out)


def test_cost_is_exact_for_large_usage() -> None:
    p = Price("m", date(2026, 1, 1), Decimal("15.00"), Decimal("75.00"))
    usage = Usage(input_tokens=123_456_789, output_tokens=987_654_321)
    # 123.456789 * 15 + 987.654321 * 75 = 1851.851835 + 74074.074075
    assert cost(usage, p) == Decimal("75925.925910")


def test_cost_never_goes_through_float() -> None:
    # 9999999999.999999 needs 16 significant digits; a float round-trip gives 10000000000.0.
    p = Price("m", date(2026, 1, 1), Decimal("1.00"), Decimal("0"))
    usage = Usage(input_tokens=9_999_999_999_999_999, output_tokens=0)
    assert str(cost(usage, p)) == "9999999999.999999"


def test_app_prices_fake_echo_by_default(client: TestClient) -> None:
    prices = client.app.state.gateway.prices  # type: ignore[attr-defined]
    price = prices.price_for("fake-echo", date(2026, 1, 1))
    assert (price.input_per_mtok, price.output_per_mtok) == (Decimal("1.00"), Decimal("2.00"))
    with pytest.raises(NoPriceError):
        prices.price_for("fake-echo", date(2025, 12, 31))
