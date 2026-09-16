#!/usr/bin/env python3
"""Persistent live-signal history and comparable-setup statistics.

Stores emitted signals and later outcomes so live alerts can show a genuine
historical success rate for similar setups. Statistics are descriptive only.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

PATH = "state/signal_history.jsonl"


def _ts():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read():
    rows = []
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if isinstance(r, dict): rows.append(r)
                except json.JSONDecodeError:
                    continue
    except (FileNotFoundError, OSError):
        pass
    return rows


def _write(row):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_signal(signal):
    """Record a live signal once; return its stable signal id."""
    sid = signal_id(signal)
    existing = {r.get("id") for r in _read() if r.get("kind") == "signal"}
    if sid not in existing:
        _write({"kind": "signal", "id": sid, "ts": _ts(),
                "coin": signal.get("coin"), "interval": signal.get("interval"),
                "setup_type": signal.get("setup_type"),
                "score": float(signal.get("score", 0)),
                "entry": float(signal.get("entry", 0)),
                "stop": float(signal.get("stop", 0)),
                "target": float(signal.get("target", signal.get("t1", 0))),
                "potential_pct": float(signal.get("potential_pct", 0)),
                "rr": float(signal.get("rr", 0)),
                "trend_4h": signal.get("trend_4h")})
    return sid


def signal_id(signal):
    return "|".join(str(signal.get(k, "")) for k in ("coin", "interval", "setup_type", "entry"))


def record_outcome(signal, outcome, exit_price=None):
    sid = signal_id(signal)
    rows = _read()
    if any(r.get("kind") == "outcome" and r.get("id") == sid for r in rows):
        return
    _write({"kind": "outcome", "id": sid, "ts": _ts(),
            "outcome": str(outcome).upper(),
            "exit_price": exit_price})


def comparable_stats(signal, min_samples=20):
    """Return stats for same coin/timeframe/setup, with setup-level fallback."""
    rows = _read()
    signals = {r["id"]: r for r in rows if r.get("kind") == "signal" and r.get("id")}
    outcomes = {r["id"]: r for r in rows if r.get("kind") == "outcome" and r.get("id")}

    exact = []
    fallback = []
    for sid, out in outcomes.items():
        s = signals.get(sid)
        if not s: continue
        if out.get("outcome") not in ("WIN", "LOSS"): continue
        if s.get("coin") == signal.get("coin") and s.get("interval") == signal.get("interval") and s.get("setup_type") == signal.get("setup_type"):
            exact.append(out.get("outcome"))
        if s.get("interval") == signal.get("interval") and s.get("setup_type") == signal.get("setup_type"):
            fallback.append(out.get("outcome"))

    sample = exact if len(exact) >= min_samples else fallback
    scope = "exact" if len(exact) >= min_samples else "setup/timeframe"
    if not sample:
        return {"win_pct": None, "wins": 0, "losses": 0, "sample": 0, "scope": scope}
    wins = sample.count("WIN")
    losses = sample.count("LOSS")
    return {"win_pct": wins / len(sample) * 100, "wins": wins,
            "losses": losses, "sample": len(sample), "scope": scope}


def backfill_from_backtest(results):
    """Import completed backtest trades into history for baseline statistics."""
    for result in results or []:
        for trade in result.get("trades", []):
            signal = {"coin": result.get("symbol"), "interval": result.get("interval"),
                      "setup_type": trade.get("setup_type"), "entry": trade.get("entry"),
                      "score": trade.get("score", 0), "potential_pct": trade.get("potential_pct", 0),
                      "rr": trade.get("rr", 0), "stop": trade.get("stop", 0), "target": trade.get("target", 0),
                      "trend_4h": trade.get("trend_4h")}
            record_signal(signal)
            record_outcome(signal, trade.get("outcome"), trade.get("exit_price"))
