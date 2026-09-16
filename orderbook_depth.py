"""Conservative full-depth SPOT order-book simulation.

The simulator consumes normalized asks/bids and never assumes that the BBO
quantity represents the whole executable notional. It is intentionally
exchange-agnostic so REST snapshots and future websocket depth feeds can share
it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

Level = Tuple[float, float]


@dataclass(frozen=True)
class FillSimulation:
    requested_quote: float
    spent_quote: float
    acquired_base: float
    sold_base: float
    received_quote: float
    average_buy: float
    average_sell: float
    complete: bool
    levels_used_buy: int
    levels_used_sell: int


def _levels(rows: Iterable[Sequence[float]], reverse: bool):
    parsed = []
    for row in rows:
        if len(row) < 2:
            continue
        price, qty = float(row[0]), float(row[1])
        if price > 0 and qty > 0:
            parsed.append((price, qty))
    return sorted(parsed, reverse=reverse)


def buy_from_asks(asks: Iterable[Sequence[float]], quote_notional: float) -> tuple[float, float, int, bool]:
    """Return (base acquired, quote spent, levels, complete)."""
    if quote_notional <= 0:
        return 0.0, 0.0, 0, False
    remaining = float(quote_notional)
    base = spent = 0.0
    used = 0
    for price, qty in _levels(asks, reverse=False):
        take_quote = min(remaining, price * qty)
        take_base = take_quote / price
        base += take_base
        spent += take_quote
        remaining -= take_quote
        used += 1
        if remaining <= 1e-12:
            return base, spent, used, True
    return base, spent, used, False


def sell_into_bids(bids: Iterable[Sequence[float]], base_qty: float) -> tuple[float, int, bool]:
    """Return (quote received, levels, complete)."""
    if base_qty <= 0:
        return 0.0, 0, False
    remaining = float(base_qty)
    received = 0.0
    used = 0
    for price, qty in _levels(bids, reverse=True):
        take = min(remaining, qty)
        received += take * price
        remaining -= take
        used += 1
        if remaining <= 1e-12:
            return received, used, True
    return received, used, False


def simulate_round_trip(asks: Iterable[Sequence[float]], bids: Iterable[Sequence[float]], quote_notional: float) -> FillSimulation:
    base, spent, buy_levels, buy_complete = buy_from_asks(asks, quote_notional)
    received, sell_levels, sell_complete = sell_into_bids(bids, base)
    avg_buy = spent / base if base else 0.0
    avg_sell = received / base if base else 0.0
    return FillSimulation(quote_notional, spent, base, base if sell_complete else 0.0,
                          received if sell_complete else 0.0, avg_buy, avg_sell,
                          buy_complete and sell_complete, buy_levels, sell_levels)
