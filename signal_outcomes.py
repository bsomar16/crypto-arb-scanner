#!/usr/bin/env python3
"""Live signal outcome tracker with causal MFE/MAE and expansion milestones.

The tracker only evaluates candles that closed after a signal was emitted.
It never feeds future candles back into the signal itself; outcomes are
post-entry measurements used for historical evidence and diagnostics.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import signal_history

BINANCE = "https://api.binance.com"
STATE_PATH = "state/signal_outcomes.json"
REFRESH_PATH = "state/signal_outcomes_refresh.json"
MILESTONES = (5, 10, 20, 30, 50, 80)
DEFAULT_HORIZON_BARS = {"5m": 288, "15m": 96, "1h": 48}
INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
            return value if isinstance(value, type(default)) else default
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def _save(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fetch_closed(symbol, interval, limit=500):
    url = f"{BINANCE}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
    req = Request(url, headers={"User-Agent": "crypto-arb-scanner/1.0"})
    with urlopen(req, timeout=15) as response:
        rows = json.loads(response.read().decode("utf-8"))
    if not isinstance(rows, list):
        return []
    # REST returns the currently forming candle as the last row. Exclude it.
    return [r for r in rows[:-1] if len(r) >= 6]


def _evaluate(signal, rows, horizon_bars):
    entry_time = int(signal.get("candle_open_time") or 0)
    if not entry_time:
        return None

    start = next((i for i, row in enumerate(rows) if int(row[0]) == entry_time), None)
    if start is None:
        return None

    future = rows[start + 1:]
    if not future:
        return None

    future = future[:max(1, int(horizon_bars))]
    entry = float(signal.get("entry") or 0)
    stop = float(signal.get("stop") or 0)
    target = float(signal.get("target") or 0)
    if entry <= 0 or stop <= 0 or target <= entry:
        return None

    mfe = 0.0
    mae = 0.0
    reached = {str(p): False for p in MILESTONES}

    for row in future:
        high = float(row[2])
        low = float(row[3])
        mfe = max(mfe, (high / entry - 1.0) * 100.0)
        mae = min(mae, (low / entry - 1.0) * 100.0)

        # Conservative ordering: if the same candle touches SL and a target,
        # SL is treated as first because intrabar ordering is unknowable.
        if low <= stop:
            return {
                "outcome": "LOSS",
                "exit_price": stop,
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
                "bars_held": future.index(row) + 1,
                "milestones": reached,
                "evaluated_at": _now(),
            }

        for pct in MILESTONES:
            if high >= entry * (1.0 + pct / 100.0):
                reached[str(pct)] = True

        if high >= target:
            return {
                "outcome": "WIN",
                "exit_price": target,
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
                "bars_held": future.index(row) + 1,
                "milestones": reached,
                "evaluated_at": _now(),
            }

    # Do not close an outcome until the full horizon is available.
    if len(future) < max(1, int(horizon_bars)):
        return None

    last = float(future[-1][4])
    return {
        "outcome": "EXPIRED",
        "exit_price": last,
        "mfe_pct": round(mfe, 3),
        "mae_pct": round(mae, 3),
        "bars_held": len(future),
        "milestones": reached,
        "evaluated_at": _now(),
    }


def update_pending(cfg=None, force=False):
    """Update a bounded set of live signals; return counts by outcome.

    The operation is throttled so realtime scans do not repeatedly hit REST.
    """
    cfg = cfg or {}
    if not bool(cfg.get("outcome_tracking_enabled", True)):
        return {"updated": 0, "pending": 0, "errors": 0}

    refresh_seconds = max(60, int(cfg.get("outcome_refresh_seconds", 300)))
    refresh = _load(REFRESH_PATH, {})
    if not force and time.time() - float(refresh.get("ts", 0) or 0) < refresh_seconds:
        return {"updated": 0, "pending": 0, "errors": 0}

    rows = signal_history._read()
    signals = [r for r in rows if r.get("kind") == "signal" and r.get("id")]
    outcomes = {r.get("id"): r for r in rows if r.get("kind") == "outcome" and r.get("id")}
    pending = [s for s in signals if s["id"] not in outcomes and s.get("candle_open_time")]

    max_pending = max(1, int(cfg.get("outcome_max_pending", 60)))
    pending = pending[-max_pending:]
    horizon_cfg = cfg.get("outcome_horizon_bars", {}) or {}
    state = _load(STATE_PATH, {})
    updated = errors = 0

    for signal in pending:
        interval = str(signal.get("interval", "15m"))
        horizon = int(horizon_cfg.get(interval, DEFAULT_HORIZON_BARS.get(interval, 96)))
        try:
            klines = _fetch_closed(str(signal.get("coin")), interval, limit=max(100, min(500, horizon + 80)))
            result = _evaluate(signal, klines, horizon)
            if result is None:
                continue
            details = dict(result)
            signal_history.record_outcome(
                signal,
                result["outcome"],
                result.get("exit_price"),
                details=details,
            )
            state[signal["id"]] = {
                **details,
                "coin": signal.get("coin"),
                "interval": interval,
                "setup_type": signal.get("setup_type"),
                "updated_at": _now(),
            }
            updated += 1
        except Exception:
            errors += 1

    _save(STATE_PATH, state)
    _save(REFRESH_PATH, {"ts": time.time(), "updated_at": _now()})
    counts = {"updated": updated, "pending": len(pending), "errors": errors}
    return counts
