#!/usr/bin/env python3
"""Provider-neutral SPOT order constraint validation and quantization."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import Optional

from exchange_adapter import SpotMarket


class OrderConstraintError(ValueError):
    """Raised when a SPOT order cannot satisfy the exchange's market rules."""


@dataclass(frozen=True)
class NormalizedOrder:
    quantity: float
    price: Optional[float]
    notional: float


def _d(value: float | int | str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OrderConstraintError(f"invalid numeric constraint/value: {value!r}") from exc


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def normalize_spot_order(market: SpotMarket, quantity: float, price: Optional[float] = None) -> NormalizedOrder:
    """Quantize down and validate min/max quantity, price tick and min notional.

    Quantity is always rounded down so the adapter never asks the exchange for
    more base asset than the caller authorized. A result that falls below the
    exchange minimum is rejected rather than rounded up.
    """
    qty = _d(quantity)
    if qty <= 0:
        raise OrderConstraintError("quantity must be positive")

    step = _d(market.qty_step)
    min_qty = _d(market.min_qty)
    qty = _floor_step(qty, step)
    if qty < min_qty:
        raise OrderConstraintError(f"quantity {qty} is below min_qty {min_qty}")

    # SpotMarket intentionally has no max_qty field. Exchange adapters should
    # expose a bounded max through a future contract extension if required.
    px: Optional[Decimal] = None
    if price is not None:
        px = _d(price)
        if px <= 0:
            raise OrderConstraintError("price must be positive")
        tick = _d(market.price_tick)
        px = _floor_step(px, tick)
        if px <= 0:
            raise OrderConstraintError("price becomes zero after tick quantization")

    notional = qty * px if px is not None else Decimal("0")
    min_notional = _d(market.min_notional)
    if px is not None and min_notional > 0 and notional < min_notional:
        raise OrderConstraintError(
            f"order notional {notional} is below min_notional {min_notional}"
        )

    return NormalizedOrder(float(qty), float(px) if px is not None else None, float(notional))
