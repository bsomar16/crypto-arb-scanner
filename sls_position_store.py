"""Persistent SLS outcome ledger for research and outcome tracking.

Persists open and closed SLS positions in state/sls_positions.json.
Enforces Pine v9 stop-first priority, trailing stop updates, and 5-target progression.
Never places orders on any exchange.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Any, Set
from botutil import load_json, save_json

PATH = "state/sls_positions.json"
LIFETIME = {"1m": 120, "5m": 120, "15m": 120, "30m": 120, "45m": 120, "1h": 120, "4h": 120, "1d": 40, "1w": 12}
C_FEE = 0.002
C_SLOT = 0.2


def load_state() -> Dict[str, Any]:
    data = load_json(PATH, {}) or {}
    return data if isinstance(data, dict) else {}


def save_state(state: Dict[str, Any]):
    save_json(PATH, state)


def key(coin: str, interval: str) -> str:
    return f"{str(coin).upper()}:{interval}"


def advance_position(state: Dict[str, Any], coin: str, interval: str,
                     rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Advance open SLS position using newly closed candles."""
    k = key(coin, interval)
    p = state.get(k)
    if not p or p.get("status") != "open":
        return []

    events = []
    entry = float(p["entry"])
    stop = float(p["stop"])
    targets = [float(x) for x in p["targets"]]
    risk = entry - stop if entry - stop > 0 else entry * 0.01
    cost_r = C_FEE * entry / risk

    for row in rows or []:
        ts = int(row["t"])
        if ts <= int(p.get("last_bar_open", p["opened_bar_open"])):
            continue

        low_p = float(row["l"])
        high_p = float(row["h"])
        close_p = float(row["c"])

        # 1. Stop loss check (Pine ordering: Stop wins over targets if touched on same bar)
        if low_p <= stop:
            is_breakeven = stop >= entry
            p["status"] = "closed"
            p["close_reason"] = "TRAIL" if is_breakeven else "STOP"
            p["close_bar_open"] = ts
            p["final_r"] = round(float(p.get("realized_r", 0.0)) + float(p.get("remaining_fraction", 1.0)) * ((stop - entry) / risk) - cost_r, 2)
            events.append({
                "event": p["close_reason"], "coin": coin, "interval": interval,
                "price": stop, "bar_open": ts, "r_mult": p["final_r"]
            })
            break

        # 2. Targets check
        hits_updated = False
        for n in range(1, 6):
            field = f"tp{n}"
            target_px = targets[n - 1]
            if not p.get(field) and high_p >= target_px:
                p[field] = True
                p["realized_r"] = float(p.get("realized_r", 0.0)) + C_SLOT * ((target_px - entry) / risk)
                p["remaining_fraction"] = float(p.get("remaining_fraction", 1.0)) - C_SLOT
                hits_updated = True
                events.append({
                    "event": field.upper(), "coin": coin, "interval": interval,
                    "price": target_px, "bar_open": ts
                })

        # 3. Dynamic trailing stop update
        n_hits = sum(1 for n in range(1, 6) if p.get(f"tp{n}"))
        new_sl = targets[1] if n_hits >= 3 else targets[0] if n_hits >= 2 else entry if n_hits >= 1 else stop
        if new_sl > stop:
            stop = new_sl
            p["stop"] = stop

        # 4. All targets reached
        if n_hits >= 5:
            p["status"] = "closed"
            p["close_reason"] = "ALL_TARGETS"
            p["close_bar_open"] = ts
            p["final_r"] = round(float(p.get("realized_r", 0.0)) - cost_r, 2)
            events.append({
                "event": "CLOSE", "reason": "ALL_TARGETS", "coin": coin,
                "interval": interval, "bar_open": ts, "r_mult": p["final_r"]
            })
            break

        # 5. Lifetime timeout
        p["bars_elapsed"] = int(p.get("bars_elapsed", 0)) + 1
        if p["bars_elapsed"] > int(p["lifetime_bars"]):
            p["status"] = "closed"
            p["close_reason"] = "TIMEOUT"
            p["close_bar_open"] = ts
            p["final_r"] = round(float(p.get("realized_r", 0.0)) + float(p.get("remaining_fraction", 1.0)) * ((close_p - entry) / risk) - cost_r, 2)
            events.append({
                "event": "CLOSE", "reason": "TIMEOUT", "coin": coin,
                "interval": interval, "bar_open": ts, "r_mult": p["final_r"]
            })
            break

        p["last_bar_open"] = ts

    return events


def register_signal(state: Dict[str, Any], signal: Dict[str, Any]) -> bool:
    """Register confirmed SLS BUY signal."""
    if str(signal.get("strategy", "")).upper() != "SLS" or str(signal.get("signal_action", "BUY")).upper() != "BUY":
        return False
    coin = str(signal.get("coin") or "").upper()
    interval = str(signal.get("interval") or "")
    if not coin or not interval:
        return False

    k = key(coin, interval)
    old = state.get(k)
    if old and old.get("status") == "open":
        return False

    entry = float(signal["entry"])
    stop = float(signal.get("stop", entry * 0.98))
    targets = [
        float(signal.get("t1", entry * 1.02)),
        float(signal.get("t2", entry * 1.04)),
        float(signal.get("t3", entry * 1.06)),
        float(signal.get("t4", entry * 1.08)),
        float(signal.get("t5", entry * 1.10)),
    ]

    state[k] = {
        "status": "open",
        "coin": coin,
        "interval": interval,
        "entry": entry,
        "stop": stop,
        "targets": targets,
        "opened_bar_open": int(signal["candle_open_time"]),
        "last_bar_open": int(signal["candle_open_time"]),
        "bars_elapsed": 0,
        "lifetime_bars": LIFETIME.get(interval, 120),
        "setup_type": signal.get("setup_type", "SLS"),
        "score": signal.get("score", 0),
        "realized_r": 0.0,
        "remaining_fraction": 1.0,
        "tp1": False,
        "tp2": False,
        "tp3": False,
        "tp4": False,
        "tp5": False,
        "signal_source": "SLS",
        "signal_engine": "Spot Long Signal Engine v9",
    }
    return True


def active_keys(state: Dict[str, Any]) -> Set[str]:
    return {k for k, p in state.items() if p.get("status") == "open"}

