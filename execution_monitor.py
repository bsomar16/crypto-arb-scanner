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
    raw: Any


def normalize_order_status(raw: dict) -> str:
    status = str(raw.get("status") or raw.get("orderStatus") or "").upper()
    mapping = {
        "NEW": "OPEN",
        "OPEN": "OPEN",
        "PARTIALLY_FILLED": "PARTIAL",
        "PARTIALLYFILLED": "PARTIAL",
        "PARTIAL": "PARTIAL",
        "FILLED": "FILLED",
        "CANCELED": "CANCELLED",
        "CANCELLED": "CANCELLED",
        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }
    return mapping.get(status, status or "UNKNOWN")


def reconcile_order(adapter: Any, symbol: str, order_id: str) -> OrderSnapshot:
    """Read one authenticated SPOT order and normalize provider differences."""
    raw = adapter.get_order(symbol, str(order_id))
    executed = raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0)))
    avg = raw.get("avgPrice", raw.get("averagePrice", raw.get("price", 0)))
    try:
        executed_f = float(executed or 0)
    except (TypeError, ValueError):
        executed_f = 0.0
    try:
        avg_f = float(avg or 0)
    except (TypeError, ValueError):
        avg_f = 0.0
    return OrderSnapshot(normalize_order_status(raw), executed_f, avg_f, raw)


def order_transition(snapshot: OrderSnapshot, submitted_state: str) -> str:
    """Translate an exchange snapshot into the controlled state machine."""
    if snapshot.status == "FILLED":
        return "FILLED"
    if snapshot.status == "PARTIAL":
        return "PARTIAL"
    if snapshot.status in {"CANCELLED", "REJECTED", "EXPIRED"}:
        return "FAILED"
    return submitted_state
