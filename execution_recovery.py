#!/usr/bin/env python3
"""Persistent recovery and safety controls for controlled SPOT execution."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}
ACTIVE = {"PENDING_CONFIRMATION", "DRY_RUN_CONFIRMED", "READY_FOR_ADAPTER", "BUY_SUBMITTED", "BUY_PARTIAL", "TRANSFER_PENDING", "SELL_SUBMITTED", "SELL_PARTIAL"}


class ExecutionSafety:
    def __init__(self, cfg: dict, state_dir: str = "state"):
        self.cfg = cfg
        self.state = Path(state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.kill_switch = os.getenv("EXECUTION_KILL_SWITCH", "false").lower() == "true"
        self.max_notional = float(cfg.get("execution_max_notional_usdt", 300.0))
        self.max_active = int(cfg.get("execution_max_active_intents", 1))
        self.max_daily_notional = float(cfg.get("execution_max_daily_notional_usdt", 1000.0))

    def assert_allowed(self, notional: float, active_count: int, daily_notional: float) -> None:
        if self.kill_switch:
            raise PermissionError("execution kill switch is enabled")
        if notional <= 0 or notional > self.max_notional:
            raise ValueError("execution notional exceeds safety limit")
        if active_count >= self.max_active:
            raise RuntimeError("maximum active execution intents reached")
        if daily_notional + notional > self.max_daily_notional:
            raise RuntimeError("daily execution notional limit reached")

    @staticmethod
    def transition(current: str, target: str) -> None:
        allowed = {
            "PENDING_CONFIRMATION": {"DRY_RUN_CONFIRMED", "READY_FOR_ADAPTER", "CANCELLED", "EXPIRED"},
            "DRY_RUN_CONFIRMED": {"COMPLETED", "FAILED", "CANCELLED"},
            "READY_FOR_ADAPTER": {"BUY_SUBMITTED", "FAILED", "CANCELLED"},
            "BUY_SUBMITTED": {"BUY_PARTIAL", "TRANSFER_PENDING", "FAILED", "CANCELLED"},
            "BUY_PARTIAL": {"TRANSFER_PENDING", "FAILED"},
            "TRANSFER_PENDING": {"SELL_SUBMITTED", "FAILED"},
            "SELL_SUBMITTED": {"SELL_PARTIAL", "COMPLETED", "FAILED"},
            "SELL_PARTIAL": {"COMPLETED", "FAILED"},
        }
        if target not in allowed.get(current, set()):
            raise ValueError(f"invalid execution transition: {current} -> {target}")


def recover_active_intents(path: str = "state/execution_intents.jsonl") -> Dict[str, dict]:
    """Return the latest record for every non-terminal intent after a restart."""
    p = Path(path)
    if not p.exists():
        return {}
    latest: Dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id"):
            latest[row["id"]] = row
    return {k: v for k, v in latest.items() if v.get("status") not in TERMINAL}


def now_ms() -> int:
    return int(time.time() * 1000)
