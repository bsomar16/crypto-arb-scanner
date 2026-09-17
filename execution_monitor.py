#!/usr/bin/env python3
"""Provider-neutral SPOT execution reconciliation."""
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
    status = str(raw.get("status") or raw.get("orderStatus") or raw.get("state") or raw.get("orderState") or "").upper()
    mapping = {
        "NEW": "OPEN", "OPEN": "OPEN", "LIVE": "OPEN", "ACTIVE": "OPEN",
        "PARTIALLY_FILLED": "PARTIAL", "PARTIALLYFILLED": "PARTIAL", "PARTIAL": "PARTIAL",
        "FILLED": "FILLED", "FULLY_FILLED": "FILLED",
        "CANCELED": "CANCELLED", "CANCELLED": "CANCELLED", "CANCEL": "CANCELLED",
        "REJECTED": "REJECTED", "EXPIRED": "EXPIRED", "EXPIRE": "EXPIRED",
    }
    return mapping.get(status, status or "UNKNOWN")


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _first_number(raw: dict, keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            value = _number(raw[key])
            if value or str(raw[key]) in ("0", "0.0"):
                return value
    return 0.0


def _fee(raw: dict) -> tuple[float, str]:
    amount = _first_number(raw, (
        "feeAmount", "cumFee", "cumExecFee", "fee", "feeQuote", "fillFee",
        "totalFee", "cumFeeAmt", "commission",
    ))
    currency = str(raw.get("feeCurrency") or raw.get("feeCcy") or raw.get("cumFeeCurrency")
                   or raw.get("feeAsset") or raw.get("fillFeeCcy") or raw.get("commissionAsset") or "")
    if not amount and isinstance(raw.get("cumFeeDetail"), dict):
        for ccy, value in raw["cumFeeDetail"].items():
            amount = _number(value)
            currency = str(ccy)
            if amount:
                break
    fills = raw.get("fills") or raw.get("trades") or raw.get("fillDetails") or []
    if fills and not amount:
        total = 0.0
        ccy = ""
        for fill in fills:
            total += _first_number(fill, ("commission", "feeAmount", "fee", "fillFee", "totalFee"))
            ccy = str(fill.get("commissionAsset") or fill.get("feeCurrency")
                      or fill.get("fillFeeCcy") or fill.get("feeCcy") or ccy)
        amount, currency = total, ccy
    return abs(amount), currency


def reconcile_order(adapter: Any, symbol: str, order_id: str) -> OrderSnapshot:
    raw = adapter.get_order(symbol, str(order_id)) or {}
    executed = _first_number(raw, (
        "executedQty", "cumExecQty", "filledQty", "dealQuantity", "cumulativeQuantity",
        "accFillSz", "baseVolume", "filledSize", "dealSize", "executedSize",
    ))
    avg = _first_number(raw, (
        "avgPrice", "averagePrice", "avgPx", "dealAvgPrice", "priceAvg", "fillPrice", "avgFillPrice", "price",
    ))
    quote_qty = _first_number(raw, (
        "cummulativeQuoteQty", "cumExecValue", "cumulativeAmount", "quoteQty", "accFillValue",
        "quoteVolume", "filledQuoteQty", "dealAmount", "executedQuoteQty",
    ))
    fee_amount, fee_currency = _fee(raw)
    return OrderSnapshot(
        normalize_order_status(raw), executed, avg, fee_amount, fee_currency, quote_qty,
        str(raw.get("orderId") or raw.get("order_id") or raw.get("ordId") or raw.get("orderIdStr")
            or raw.get("id") or order_id), raw,
    )


def order_transition(snapshot: OrderSnapshot, submitted_state: str) -> str:
    if snapshot.status == "FILLED":
        return "FILLED"
    if snapshot.status == "PARTIAL":
        return "PARTIAL"
    if snapshot.status in {"CANCELLED", "REJECTED", "EXPIRED"}:
        return "FAILED"
    return submitted_state
