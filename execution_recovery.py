from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}
ACTIVE = {
    "PENDING_CONFIRMATION", "DRY_RUN_CONFIRMED", "READY_FOR_ADAPTER",
    "BUY_SUBMITTED", "BUY_PARTIAL", "BUY_FILLED", "TRANSFER_PENDING",
    "TRANSFER_CONFIRMED", "SELL_SUBMITTED", "SELL_PARTIAL", "SELL_FILLED",
}

TRANSITIONS = {
    "PENDING_CONFIRMATION": {"DRY_RUN_CONFIRMED", "READY_FOR_ADAPTER", "CANCELLED", "EXPIRED"},
    "DRY_RUN_CONFIRMED": {"COMPLETED", "FAILED", "CANCELLED"},
    "READY_FOR_ADAPTER": {"BUY_SUBMITTED", "FAILED", "CANCELLED"},
    "BUY_SUBMITTED": {"BUY_PARTIAL", "BUY_FILLED", "FAILED", "CANCELLED"},
    "BUY_PARTIAL": {"BUY_SUBMITTED", "BUY_FILLED", "FAILED", "CANCELLED"},
    "BUY_FILLED": {"TRANSFER_PENDING", "FAILED", "CANCELLED"},
    "TRANSFER_PENDING": {"TRANSFER_CONFIRMED", "FAILED", "CANCELLED"},
    "TRANSFER_CONFIRMED": {"SELL_SUBMITTED", "FAILED", "CANCELLED"},
    "SELL_SUBMITTED": {"SELL_PARTIAL", "SELL_FILLED", "FAILED", "CANCELLED"},
    "SELL_PARTIAL": {"SELL_SUBMITTED", "SELL_FILLED", "FAILED", "CANCELLED"},
    "SELL_FILLED": {"COMPLETED", "FAILED"},
}

@dataclass
class ExecutionSafety:
    max_notional_usdt: float = 300.0
    max_active_intents: int = 2
    max_daily_notional_usdt: float = 1000.0
    kill_switch: bool = False

    @classmethod
    def from_env(cls) -> "ExecutionSafety":
        return cls(
            max_notional_usdt=float(os.getenv("EXECUTION_MAX_NOTIONAL_USDT", "300")),
            max_active_intents=int(os.getenv("EXECUTION_MAX_ACTIVE_INTENTS", "2")),
            max_daily_notional_usdt=float(os.getenv("EXECUTION_MAX_DAILY_NOTIONAL_USDT", "1000")),
            kill_switch=os.getenv("EXECUTION_KILL_SWITCH", "false").lower() in {"1", "true", "yes", "on"},
        )

    def validate_notional(self, notional: float) -> None:
        if self.kill_switch:
            raise ValueError("execution kill switch is active")
        if notional <= 0 or notional > self.max_notional_usdt:
            raise ValueError("notional exceeds execution safety limit")


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition(record: dict[str, Any], target: str) -> dict[str, Any]:
    current = str(record.get("status", "PENDING_CONFIRMATION"))
    if current in TERMINAL:
        raise ValueError(f"terminal intent cannot transition: {current}")
    if not can_transition(current, target):
        raise ValueError(f"invalid transition: {current} -> {target}")
    record["status"] = target
    return record


def recover_active_intents(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        intent_id = str(record.get("intent_id", ""))
        if intent_id:
            latest[intent_id] = record
    return {k: v for k, v in latest.items() if str(v.get("status")) in ACTIVE}


def append_record(path: str | Path, record: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def load_records(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records
