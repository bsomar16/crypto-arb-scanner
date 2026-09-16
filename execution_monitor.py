#!/usr/bin/env python3
"""Provider-neutral SPOT execution reconciliation.

The monitor deliberately depends only on the SPOT adapter contract. It polls
order status as a restart-safe fallback; exchanges can later attach private
WebSocket streams without changing the execution state model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrderSnapshot:
    status: str
    executed_qty: float
    avg_price: float
    fee_amount: float
    fee_currency: str
    executed_quote_qty: float
    provider_order_id: str
    raw: Any


def normalize_order_status(raw: dict) -> str:
    status = str(raw.get("status") or raw.get("orderStatus") or "").upper()
    mapping = {
        "NEW": "OPEN", "OPEN": "OPEN",
        "PARTIALLY_FILLED": "PARTIAL", "PARTIALLYFILLED": "PARTIAL", "PARTIAL": "PARTIAL",
        "FILLED": "FILLED", "CANCELED": "CANCELLED", "CANCELLED": "CANCELLED",
        "REJECTED": "REJECTED", "EXPIRED": "EXPIRED",
    }
    return mapping.get(status, status or "UNKNOWN")


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fee(raw: dict) -> tuple[float, str]:
    """Extract the cumulative fee when the provider exposes it."""
    amount = _number(raw.get("feeAmount", raw.get("cumFee", raw.get("cumExecFee", raw.get("fee", raw.get("feeQuote", 0))))))
    currency = str(raw.get("feeCurrency") or raw.get("feeCcy") or raw.get("cumFeeCurrency") or raw.get("feeAsset") or "")
    if not amount and isinstance(raw.get("cumFeeDetail"), dict):
        for ccy, value in raw["cumFeeDetail"].items():
            amount = _number(value)
            currency = str(ccy)
            if amount:
                break
    # Binance/MEXC may return per-fill fee data on an order response.
    fills = raw.get("fills") or raw.get("trades") or []
    if fills and not amount:
        total = 0.0
        ccy = ""
        for fill in fills:
            total += _number(fill.get("commission", fill.get("feeAmount", fill.get("fee", 0))))
            ccy = str(fill.get("commissionAsset") or fill.get("feeCurrency") or ccy)
        amount, currency = total, ccy
    return abs(amount), currency


def reconcile_order(adapter: Any, symbol: str, order_id: str) -> OrderSnapshot:
    """Read one authenticated SPOT order and normalize provider differences."""
    raw = adapter.get_order(symbol, str(order_id))
    executed = _number(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", raw.get("dealQuantity", raw.get("cumulativeQuantity", 0))))))
    avg = _number(raw.get("avgPrice", raw.get("averagePrice", raw.get("dealAvgPrice", raw.get("price", 0)))))
    quote_qty = _number(raw.get("cummulativeQuoteQty", raw.get("cumExecValue", raw.get("cumulativeAmount", raw.get("quoteQty", 0)))))
    fee_amount, fee_currency = _fee(raw)
    return OrderSnapshot(
        normalize_order_status(raw), executed, avg, fee_amount, fee_currency, quote_qty,
        str(raw.get("orderId") or raw.get("order_id") or raw.get("ordId") or raw.get("id") or order_id), raw,
    )


def order_transition(snapshot: OrderSnapshot, submitted_state: str) -> str:
    """Translate an exchange snapshot into the controlled state machine."""
    if snapshot.status == "FILLED":
        return "FILLED"
    if snapshot.status == "PARTIAL":
        return "PARTIAL"
    if snapshot.status in {"CANCELLED", "REJECTED", "EXPIRED"}:
        return "FAILED"
    return submitted_state
