#!/usr/bin/env python3
"""Deterministic partial-take-profit state machine for SPOT positions.

This module manages quantities and risk-state only. It does not submit exchange
orders; live exits must pass through the existing execution engine and SPOT
adapter boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class TPPlan:
    tp1_allocation_pct: float = 30.0
    tp2_allocation_pct: float = 30.0
    tp3_allocation_pct: float = 40.0
    move_sl_to_breakeven: bool = True
    breakeven_buffer_pct: float = 0.0

    def validate(self) -> None:
        values = (self.tp1_allocation_pct, self.tp2_allocation_pct, self.tp3_allocation_pct)
        if any(v < 0 or v > 100 for v in values):
            raise ValueError("TP allocations must be between 0 and 100 percent")
        if abs(sum(values) - 100.0) > 1e-9:
            raise ValueError("TP allocations must total 100 percent")
        if self.breakeven_buffer_pct < 0:
            raise ValueError("breakeven buffer cannot be negative")

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "TPPlan":
        plan = cls(
            tp1_allocation_pct=float(cfg.get("tp1_allocation_pct", 30.0)),
            tp2_allocation_pct=float(cfg.get("tp2_allocation_pct", 30.0)),
            tp3_allocation_pct=float(cfg.get("tp3_allocation_pct", 40.0)),
            move_sl_to_breakeven=bool(cfg.get("move_sl_to_breakeven", True)),
            breakeven_buffer_pct=float(cfg.get("breakeven_buffer_pct", 0.0)),
        )
        plan.validate()
        return plan


@dataclass
class TPState:
    entry_qty: float
    remaining_qty: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    sl: Optional[float] = None
    breakeven_activated: bool = False

    def __post_init__(self) -> None:
        if self.entry_qty <= 0:
            raise ValueError("entry_qty must be positive")
        if self.remaining_qty < 0 or self.remaining_qty > self.entry_qty + 1e-12:
            raise ValueError("remaining_qty must be within the entry quantity")

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self)


def _allocation(plan: TPPlan, target: str) -> float:
    return {
        "tp1": plan.tp1_allocation_pct,
        "tp2": plan.tp2_allocation_pct,
        "tp3": plan.tp3_allocation_pct,
    }[target]


def hit_target(state: TPState, plan: TPPlan, target: str, entry_price: float) -> Tuple[float, Dict[str, Any]]:
    """Apply one TP hit and return (quantity_to_exit, event metadata).

    A target can only be consumed once. Quantity is calculated from the original
    entry quantity, so repeated monitor polls remain idempotent.
    """
    plan.validate()
    if target not in {"tp1", "tp2", "tp3"}:
        raise ValueError("unknown TP target")
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    attr = f"{target}_hit"
    if getattr(state, attr):
        return 0.0, {"target": target, "already_hit": True, "remaining_qty": state.remaining_qty}

    qty = state.entry_qty * _allocation(plan, target) / 100.0
    qty = min(qty, state.remaining_qty)
    state.remaining_qty = max(0.0, state.remaining_qty - qty)
    setattr(state, attr, True)

    if target == "tp1" and plan.move_sl_to_breakeven and not state.breakeven_activated:
        state.sl = entry_price * (1.0 + plan.breakeven_buffer_pct / 100.0)
        state.breakeven_activated = True

    return qty, {
        "target": target,
        "exit_qty": qty,
        "remaining_qty": state.remaining_qty,
        "breakeven_activated": state.breakeven_activated,
        "new_sl": state.sl,
    }


def load_plan(cfg: Dict[str, Any]) -> TPPlan:
    return TPPlan.from_config(cfg)
