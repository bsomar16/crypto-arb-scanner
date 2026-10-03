#!/usr/bin/env python3
"""Research-only forward/shadow validation for frozen 4h hypotheses.

This module never places orders and never changes production BUY rules.
It observes the current production 4h signal engine, tags each qualified
signal with the predeclared hypotheses from the robustness studies, and
tracks simulated outcomes from closed 4h candles.
"""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from live_replay import fetch_history
from signals import intraday_signal

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ",
]
INTERVAL = "4h"
HOLD_MAX_HOURS = 120
STATE_PATH = "state/forward_shadow_4h.json"

CANDIDATES = (
    "baseline",
    "pullback",
    "pullback_and_structure_ge_50",
    "pullback_and_base",
    "pullback_structure_and_base",
    "non_expansion_and_structure_ge_50",
)


def candidate_matches(signal: dict[str, Any], candidate: str) -> bool:
    """Apply only the frozen hypothesis predicates; no threshold search."""
    setup = str(signal.get("setup_type") or signal.get("setup") or "")
    expansion = str(signal.get("expansion_state") or "")
    structure = float(signal.get("structure_score", 0.0) or 0.0)

    if candidate == "baseline":
        return True
    if candidate == "pullback":
        return setup == "PULLBACK"
    if candidate == "pullback_and_structure_ge_50":
        return setup == "PULLBACK" and structure >= 50.0
    if candidate == "pullback_and_base":
        return setup == "PULLBACK" and expansion == "BASE"
    if candidate == "pullback_structure_and_base":
        return setup == "PULLBACK" and structure >= 50.0 and expansion == "BASE"
    if candidate == "non_expansion_and_structure_ge_50":
        return expansion != "EXPANSION" and structure >= 50.0
    raise ValueError(f"unknown frozen candidate: {candidate}")


def _bar_time(bar: list[Any]) -> int:
    return int(bar[0])


def resolve_position(position: dict[str, Any], bars: list[list[Any]]) -> dict[str, Any] | None:
    """Resolve a shadow position using subsequent closed 4h candles.

    If stop and TP3 are both touched in the same candle, stop wins
    conservatively because OHLC data does not reveal intrabar ordering.
    """
    entry_time = int(position["candle_open_time"])
    entry = float(position["entry"])
    stop = float(position["stop"])
    target = float(position["t3"])
    max_age_ms = HOLD_MAX_HOURS * 60 * 60 * 1000

    future = [b for b in bars if _bar_time(b) > entry_time]
    if not future:
        return None

    mfe = float(position.get("mfe_pct", 0.0) or 0.0)
    mae = float(position.get("mae_pct", 0.0) or 0.0)
    for bar in future:
        high = float(bar[2])
        low = float(bar[3])
        mfe = max(mfe, (high / entry - 1.0) * 100.0)
        mae = min(mae, (low / entry - 1.0) * 100.0)

        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop:
            return {
                **position,
                "status": "CLOSED",
                "outcome": "LOSS",
                "exit_price": stop,
                "exit_reason": "STOP",
                "closed_candle_open_time": _bar_time(bar),
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
            }
        if hit_target:
            return {
                **position,
                "status": "CLOSED",
                "outcome": "WIN",
                "exit_price": target,
                "exit_reason": "TP3",
                "closed_candle_open_time": _bar_time(bar),
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
            }

        if _bar_time(bar) - entry_time >= max_age_ms:
            close_price = float(bar[4])
            return {
                **position,
                "status": "CLOSED",
                "outcome": "EXPIRED",
                "exit_price": close_price,
                "exit_reason": "MAX_HOLD",
                "closed_candle_open_time": _bar_time(bar),
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
            }

    return {
        **position,
        "status": "OPEN",
        "last_price": float(future[-1][4]),
        "mfe_pct": round(mfe, 3),
        "mae_pct": round(mae, 3),
    }


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "historical_only": False,
        "research_only": True,
        "candidates": list(CANDIDATES),
        "positions": [],
        "last_run_at": None,
        "runs": 0,
    }


def load_state(path: str = STATE_PATH) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError("state must be an object")
        return state
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return _default_state()


def save_state(state: dict[str, Any], path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _summary(positions: list[dict[str, Any]], candidate: str) -> dict[str, Any]:
    rows = [p for p in positions if p.get("candidate") == candidate]
    closed = [p for p in rows if p.get("status") == "CLOSED" and p.get("outcome") in {"WIN", "LOSS"}]
    wins = sum(p.get("outcome") == "WIN" for p in closed)
    losses = sum(p.get("outcome") == "LOSS" for p in closed)
    expired = sum(p.get("status") == "CLOSED" and p.get("outcome") == "EXPIRED" for p in rows)
    return {
        "tracked": len(rows),
        "open": sum(p.get("status") == "OPEN" for p in rows),
        "closed_decisive": len(closed),
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "precision_pct": round(100.0 * wins / len(closed), 2) if closed else None,
    }


def run_once(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Observe one closed-candle snapshot and update the research ledger."""
    positions = list(state.get("positions") or [])
    active = [p for p in positions if p.get("status") == "OPEN"]

    # Resolve existing positions first. Each symbol is fetched once per run.
    bars_cache: dict[str, list[list[Any]]] = {}
    for pos in active:
        symbol = str(pos["coin"]).upper()
        bars_cache.setdefault(symbol, fetch_history(symbol, INTERVAL, 180))
    for idx, pos in enumerate(positions):
        if pos.get("status") != "OPEN":
            continue
        resolved = resolve_position(pos, bars_cache.get(str(pos["coin"]).upper(), []))
        if resolved:
            positions[idx] = resolved

    # Observe current production-engine 4h signals on the same frozen universe.
    now_ms = int(time.time() * 1000)
    new_positions = 0
    for symbol in SYMBOLS:
        bars = bars_cache.get(symbol)
        if bars is None:
            bars = fetch_history(symbol, INTERVAL, 180)
            bars_cache[symbol] = bars
        trend = fetch_history(symbol, "1d", 200)
        if len(bars) < 70 or not trend:
            continue

        replay_cfg = deepcopy(cfg)
        signal = intraday_signal(
            symbol,
            interval=INTERVAL,
            limit=180,
            min_vol_x=None,
            min_hour_vol=float(cfg.get("buy_fast_min_hour_vol", 75000)),
            chg24=0,
            min_potential_pct=float(cfg.get("signal_min_potential_pct", 5)),
            max_potential_pct=float(cfg.get("signal_max_potential_pct", 300)),
            min_score=None,
            min_rr=None,
            cfg=replay_cfg,
            historical_data=bars,
            historical_trend_data=trend,
            record_history=False,
        )
        if not signal:
            continue

        candle_time = int(signal.get("candle_open_time", 0) or 0)
        if candle_time <= 0:
            continue

        for candidate in CANDIDATES:
            if not candidate_matches(signal, candidate):
                continue
            position_id = f"{symbol}-{candle_time}-{candidate}"
            if any(str(p.get("position_id")) == position_id for p in positions):
                continue
            positions.append({
                "position_id": position_id,
                "candidate": candidate,
                "status": "OPEN",
                "outcome_source": "forward_shadow",
                "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "coin": symbol,
                "interval": INTERVAL,
                "candle_open_time": candle_time,
                "entry": float(signal["entry"]),
                "stop": float(signal["stop"]),
                "t1": float(signal["t1"]),
                "t2": float(signal["t2"]),
                "t3": float(signal["t3"]),
                "score": float(signal["score"]),
                "setup_type": signal.get("setup_type"),
                "structure_score": float(signal.get("structure_score", 0.0) or 0.0),
                "expansion_state": signal.get("expansion_state"),
                "entry_quality": float(signal.get("entry_quality", 0.0) or 0.0),
                "potential_pct": float(signal.get("potential_pct", 0.0) or 0.0),
                "rr": float(signal.get("rr", 0.0) or 0.0),
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
            })
            new_positions += 1

    state["positions"] = positions
    state["last_run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["runs"] = int(state.get("runs", 0) or 0) + 1
    state["summaries"] = {candidate: _summary(positions, candidate) for candidate in CANDIDATES}
    return {"new_positions": new_positions, "summaries": state["summaries"]}


def main() -> None:
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)
    state = load_state()
    result = run_once(cfg, state)
    save_state(state)
    print(json.dumps({
        "generated_at": state["last_run_at"],
        "research_only": True,
        "result": result,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
