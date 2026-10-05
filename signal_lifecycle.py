#!/usr/bin/env python3
"""Persistent BUY-signal lifecycle state.

Lifecycle state is operational metadata, not a scoring mechanism. It prevents
stale/duplicate notifications and gives later execution/reporting code a
stable state machine without changing the Telegram message format.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

PATH = "state/signal_lifecycle.json"
STATES = ("DETECTED", "ACTIVE", "TP1", "TP2", "TP3", "STOPPED", "EXPIRED", "INVALIDATED", "CLOSED")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read():
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}


def _write(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)


def signal_key(signal):
    return "|".join(str(signal.get(k, "")) for k in ("coin", "interval", "setup_type", "entry"))


def opportunity_key(signal):
    """Keep ABT stages together without suppressing later opportunities."""
    if str(signal.get("strategy", "")).upper() != "ABT":
        return signal_key(signal)
    base = "|".join((
        str(signal.get("coin", "")).upper(),
        str(signal.get("interval", "")),
        "ABT",
    ))
    data = _read()
    row = data.get(base)
    if isinstance(row, dict) and row.get("state") in ("DETECTED", "ACTIVE", "TP1", "TP2"):
        return base
    candle = signal.get("candle_open_time") or signal.get("abt", {}).get("candle_open_time")
    return f"{base}|{int(candle)}" if candle else base


def register(signal):
    key = opportunity_key(signal)
    if not key or key.endswith("||"):
        return key
    data = _read()
    row = data.get(key)
    if not isinstance(row, dict):
        row = {
            "signal_id": key,
            "coin": signal.get("coin"),
            "interval": signal.get("interval"),
            "setup_type": signal.get("setup_type"),
            "strategy": signal.get("strategy"),
            "strategy_family": signal.get("strategy_family"),
            "stage": signal.get("strategy_family"),
            "stage_history": [],
            "entry": signal.get("entry"),
            "target": signal.get("t3", signal.get("target")),
            "t1": signal.get("t1"),
            "t2": signal.get("t2"),
            "t3": signal.get("t3", signal.get("target")),
            "detected_at": _now(),
            "state": "DETECTED",
            "events": [],
        }
        data[key] = row
    if isinstance(row, dict) and str(signal.get("strategy", "")).upper() == "ABT":
        stage = str(signal.get("strategy_family", "") or "")
        previous = str(row.get("stage", "") or "")
        if stage:
            row["stage"] = stage
            row["strategy_family"] = stage
            row.setdefault("stage_history", [])
            if stage != previous:
                row["stage_history"].append({"stage": stage, "entry": signal.get("entry"), "ts": _now()})
            row["updated_at"] = _now()
        data[key] = row
    _write(data)
    return key


def transition(signal_or_key, state, event=None, price=None):
    state = str(state or "").upper()
    if state not in STATES:
        raise ValueError(f"unsupported lifecycle state: {state}")
    key = signal_or_key if isinstance(signal_or_key, str) else register(signal_or_key)
    data = _read()
    row = data.get(key)
    if not isinstance(row, dict):
        row = {"signal_id": key, "events": []}
    row["state"] = state
    row["updated_at"] = _now()
    if event:
        row.setdefault("events", []).append({
            "event": str(event),
            "state": state,
            "ts": row["updated_at"],
            "price": price,
        })
    data[key] = row
    _write(data)
    return row


def activate(signal):
    key = register(signal)
    return transition(key, "ACTIVE", event="position_opened")


def record_target(signal_or_key, target_number, price=None):
    n = int(target_number)
    if n not in (1, 2, 3):
        raise ValueError("target_number must be 1, 2 or 3")
    return transition(signal_or_key, f"TP{n}", event=f"tp{n}_hit", price=price)


def close(signal_or_key, reason, price=None):
    reason = str(reason or "").lower()
    mapping = {
        "sl": "STOPPED",
        "stop": "STOPPED",
        "expired": "EXPIRED",
        "invalidated": "INVALIDATED",
        "tp3": "TP3",
        "closed": "CLOSED",
    }
    state = mapping.get(reason, "CLOSED")
    return transition(signal_or_key, state, event=reason or "closed", price=price)


def active_keys():
    data = _read()
    return {
        key for key, row in data.items()
        if isinstance(row, dict) and row.get("state") in ("DETECTED", "ACTIVE", "TP1", "TP2")
    }
