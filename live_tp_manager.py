#!/usr/bin/env python3
"""Controlled SPOT partial-exit manager.

The position monitor may detect a TP, but it must never bypass the execution
engine. Every live partial exit requires explicit confirmation and a fresh
revalidation immediately before the adapter call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from execution_engine import ExecutionEngine, ExecutionIntent
import trade_journal


@dataclass
class ExitResult:
    position_id: str
    target: str
    quantity: float
    price: float
    order_id: str
    status: str
    fee_quote: float = 0.0


class LiveTPManager:
    """Execute confirmed partial SPOT exits without bypassing safety gates."""

    def __init__(self, engine: ExecutionEngine):
        self.engine = engine

    @staticmethod
    def allocation(cfg: dict, target: str) -> float:
        key = {
            "TP1": "tp1_allocation_pct",
            "TP2": "tp2_allocation_pct",
            "TP3": "tp3_allocation_pct",
        }.get(str(target).upper())
        if not key:
            raise ValueError("unknown TP target")
        value = float(cfg.get(key, {"tp1_allocation_pct": 30.0,
                                    "tp2_allocation_pct": 30.0,
                                    "tp3_allocation_pct": 40.0}[key]))
        if value <= 0 or value > 100:
            raise ValueError("TP allocation must be between 0 and 100 percent")
        total = sum(float(cfg.get(k, d)) for k, d in (
            ("tp1_allocation_pct", 30.0), ("tp2_allocation_pct", 30.0),
            ("tp3_allocation_pct", 40.0)))
        if abs(total - 100.0) > 1e-9:
            raise ValueError("TP allocations must total 100 percent")
        return value / 100.0

    def execute_confirmed_exit(
        self,
        position: dict,
        target: str,
        adapter: Any,
        *,
        cfg: dict,
        explicit_confirmation: bool,
        revalidate: Callable[[ExecutionIntent], bool],
        price: Optional[float] = None,
    ) -> ExitResult:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        if not explicit_confirmation:
            raise PermissionError("explicit confirmation is required for a live TP exit")
        if position.get("status") != "open":
            raise ValueError("position is not open")
        target = str(target).upper()
        hit_key = {"TP1": "tp1_hit", "TP2": "tp2_hit", "TP3": "tp3_hit"}.get(target)
        if not hit_key:
            raise ValueError("unknown TP target")
        if position.get(hit_key):
            raise ValueError(f"{target} has already been executed")
        entry_qty = float(position.get("entry_fill_qty") or 0)
        if entry_qty <= 0:
            raise ValueError("actual entry fill quantity is required")
        remaining = float(position.get("remaining_qty", entry_qty))
        if remaining <= 0:
            raise ValueError("no remaining quantity")
        qty = min(remaining, entry_qty * self.allocation(cfg, target))
        if qty <= 0:
            raise ValueError("calculated exit quantity is zero")

        intent = ExecutionIntent(
            id=f"{position['position_id']}-{target.lower()}",
            symbol=str(position["coin"]).upper() + "USDT",
            buy_exchange=str(position.get("exchange") or "BINANCE").upper(),
            sell_exchange=str(position.get("exchange") or "BINANCE").upper(),
            notional_usdt=qty * float(price or position.get("last_price") or position["entry"]),
            buy_price=float(position["entry"]),
            sell_price=float(price or position.get("last_price") or position["entry"]),
            net_pct=float(position.get("last_pct") or 0),
            transfer_required=False,
            network=None,
            status="READY_FOR_ADAPTER",
        )
        self.engine.validate_order(intent.sell_exchange, intent.symbol, qty, "SELL", confirmed=True)
        self.engine.revalidate_before_adapter(intent, revalidate)
        raw = adapter.place_spot_order(
            intent.symbol, "SELL", qty,
            price=price or position.get("last_price"),
            order_type="LIMIT",
            client_order_id=f"tp-{position['position_id'][:24]}-{target.lower()}",
        )
        order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or "")
        status = str(raw.get("status") or "NEW").upper()
        filled = float(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0))) or 0)
        avg_price = float(raw.get("avgPrice", raw.get("price", price or 0)) or 0)
        fee = float(raw.get("fee", raw.get("feeQuote", 0)) or 0)
        if not order_id:
            raise RuntimeError("adapter returned no order id")
        if filled > 0:
            position["remaining_qty"] = max(0.0, remaining - filled)
            position[hit_key] = True
            trade_journal.record("partial_exit", position["position_id"],
                                 coin=position.get("coin"), target=target, order_id=order_id,
                                 requested_qty=qty, filled_qty=filled, executed_qty=filled,
                                 fill_price=avg_price, fee_quote=fee,
                                 remaining_qty=position["remaining_qty"], mode="live")
        return ExitResult(position["position_id"], target, qty, avg_price, order_id, status, fee)
