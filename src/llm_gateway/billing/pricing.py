"""Date-effective prices: a call is billed at the price in force on the day it was made.

Money is ``Decimal`` end to end (never float), in USD, priced per million tokens and
rounded to a micro-dollar with ROUND_HALF_UP.
"""

import bisect
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from llm_gateway.providers.base import Usage

MICRO_USD = Decimal("0.000001")
_PER_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    model: str
    effective_from: date
    input_per_mtok: Decimal
    output_per_mtok: Decimal

    def __post_init__(self) -> None:
        if self.input_per_mtok < 0 or self.output_per_mtok < 0:
            raise ValueError("prices cannot be negative")


class NoPriceError(Exception):
    """No price is in force for this model on this date."""


class PriceTable:
    def __init__(self, prices: Iterable[Price] = ()) -> None:
        # Per model, prices sorted by effective_from, so lookup is a binary search.
        self._by_model: dict[str, list[Price]] = {}
        for price in prices:
            self.add(price)

    def add(self, price: Price) -> None:
        history = self._by_model.setdefault(price.model, [])
        dates = [p.effective_from for p in history]
        if price.effective_from in dates:
            # Two prices on one day would make "the price on that day" ambiguous.
            raise ValueError(f"{price.model} already has a price from {price.effective_from}")
        history.insert(bisect.bisect(dates, price.effective_from), price)

    def price_for(self, model: str, on: date) -> Price:
        """The latest price with ``effective_from <= on``."""
        history = self._by_model.get(model, [])
        index = bisect.bisect_right([p.effective_from for p in history], on)
        if index == 0:
            raise NoPriceError(f"no price for {model} on {on}")
        return history[index - 1]


def cost(usage: Usage, price: Price) -> Decimal:
    raw = (
        usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok
    ) / _PER_MILLION
    return raw.quantize(MICRO_USD, rounding=ROUND_HALF_UP)
