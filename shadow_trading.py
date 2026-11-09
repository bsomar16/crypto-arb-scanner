#!/usr/bin/env python3
"""Paper/shadow execution ledger for confirmed crypto SPOT signals.

Shadow trading mirrors the decisions a live execution layer would make,
using public prices only. It never submits orders, transfers funds, or
changes the live execution state machine.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Callable

PATH = "state/shadow_trades.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(row: dict[str, Any], path: str = PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read(path: str = PATH) -> list[dict[str, Any]]:
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def open_trade(signal: dict[str, Any], price: float | None = None,
               notional_usdt: float = 300.0, path: str = PATH) -> dict[str, Any]:
    """Record a simulated entry; no exchange action occurs."""
    entry = float(price if price is not None else signal.get("entry", signal.get("price", 0)))
    if entry <= 0:
        raise ValueError("shadow entry price must be positive")
    row = {
        "event": "OPEN",
        "outcome_source": "shadow",
        "position_id": f"shadow-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "ts": _now(),
        "coin": str(signal.get("coin", "")).upper(),
        "interval": signal.get("interval"),
        "setup_type": signal.get("setup_type"),
        "entry": entry,
        "stop": float(signal.get("stop", signal.get("sl", entry))),
        "t1": float(signal.get("t1", entry)),
        "t2": float(signal.get("t2", entry)),
        "t3": float(signal.get("t3", signal.get("target", entry))),
        "score": signal.get("score"),
        "potential_pct": signal.get("potential_pct"),
        "rr": signal.get("rr"),
        "notional_usdt": float(notional_usdt),
        "status": "OPEN",
    }
    _write(row, path)
    return row


def close_trade(position_id: str, outcome: str, exit_price: float,
                reason: str = "", path: str = PATH) -> dict[str, Any]:
    row = {
        "event": "CLOSE",
        "outcome_source": "shadow",
        "position_id": str(position_id),
        "ts": _now(),
        "outcome": str(outcome).upper(),
        "exit_price": float(exit_price),
        "reason": reason,
        "status": "CLOSED",
    }
    _write(row, path)
    return row


def active(path: str = PATH) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for row in read(path):
        pid = row.get("position_id")
        if not pid:
            continue
        item = state.setdefault(pid, {})
        item.update({k: v for k, v in row.items() if k not in {"event", "ts"}})
    return {k: v for k, v in state.items() if v.get("status") == "OPEN"}


def run_once(signals: list[dict[str, Any]], price_fn: Callable[[str], float | None],
             cfg: dict[str, Any] | None = None, path: str = PATH) -> dict[str, Any]:
    """Open qualified signals and update existing shadow positions from prices."""
    cfg = cfg or {}
    notional = float(cfg.get("shadow_notional_usdt", 300.0))
    opened = []
    for signal in signals or []:
        if str(signal.get("rating", "")).upper() not in {"BUY", "CONFIRMED BUY", "CONFIRMED_BUY"}:
            continue
        opened.append(open_trade(signal, price_fn(str(signal.get("coin", ""))), notional, path))

    closed = []
    for pid, pos in active(path).items():
        price = price_fn(str(pos.get("coin", "")))
        if price is None:
            continue
        if price <= float(pos.get("stop", 0) or 0):
            closed.append(close_trade(pid, "LOSS", price, "STOP", path))
        elif price >= float(pos.get("t3", 0) or 0):
            closed.append(close_trade(pid, "WIN", price, "TP3", path))
    return {"opened": len(opened), "closed": len(closed), "outcome_source": "shadow"}


def statistics(path: str = PATH) -> dict[str, Any]:
    rows = read(path)
    opens = {r["position_id"]: r for r in rows if r.get("event") == "OPEN" and r.get("position_id")}
    closes = {r["position_id"]: r for r in rows if r.get("event") == "CLOSE" and r.get("position_id")}
    wins = sum(r.get("outcome") == "WIN" for r in closes.values())
    losses = sum(r.get("outcome") == "LOSS" for r in closes.values())
    return {
        "outcome_source": "shadow",
        "tracked": len(opens),
        "open": sum(pid not in closes for pid in opens),
        "closed": len(closes),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / (wins + losses) if wins + losses else None,
    }
