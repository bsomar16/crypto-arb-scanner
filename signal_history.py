#!/usr/bin/env python3
"""Persistent live-signal history and comparable-setup statistics.

Live outcomes are preferred. When live sample size is insufficient, the engine
can fall back to the compact Binance historical backtest for the same
coin/timeframe. Historical statistics are descriptive, not predictions.
"""

import json
import os
from datetime import datetime, timezone

PATH = "state/signal_history.jsonl"
BACKTEST_STATS_PATH = "state/backtest_stats.json"


def _ts():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read():
    rows = []
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if isinstance(r, dict):
                        rows.append(r)
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
                "trend_4h": signal.get("trend_4h"),
                "candle_open_time": int(signal.get("candle_open_time", signal.get("timestamp", 0)) or 0),
                "t1": float(signal.get("t1", 0) or 0),
                "t2": float(signal.get("t2", 0) or 0),
                "t3": float(signal.get("t3", signal.get("target", 0)) or 0),
                "expansion_state": signal.get("expansion_state"),
                "entry_trigger": signal.get("entry_trigger"),
                "component_flags": signal.get("component_flags", {})})
    return sid


def signal_id(signal):
    return "|".join(str(signal.get(k, "")) for k in ("coin", "interval", "setup_type", "entry"))


def record_outcome(signal, outcome, exit_price=None, details=None):
    sid = signal_id(signal)
    rows = _read()
    if any(r.get("kind") == "outcome" and r.get("id") == sid for r in rows):
        return
    _write({"kind": "outcome", "id": sid, "ts": _ts(),
            "outcome": str(outcome).upper(), "exit_price": exit_price,
            **(details or {})})


def _backtest_stats(signal):
    """Read the latest compact historical result for the same coin/timeframe."""
    try:
        with open(BACKTEST_STATS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        exact = (payload.get("stats") or {}).get(
            f"{signal.get('coin')}|{signal.get('interval')}")
        row = exact
        scope = "backtest coin/timeframe"
        sample = int((row or {}).get("wins", 0) or 0) + int((row or {}).get("losses", 0) or 0)
        if sample < 20:
            row = (payload.get("setup_stats") or {}).get(
                f"{signal.get('interval')}|{signal.get('setup_type', 'UNKNOWN')}")
            scope = "backtest setup/timeframe"
            sample = int((row or {}).get("sample", 0) or 0)
        if not row or sample <= 0 or row.get("win_pct") is None:
            return None
        return {"win_pct": float(row["win_pct"]), "wins": int(row.get("wins", 0)),
                "losses": int(row.get("losses", 0)), "sample": sample,
                "scope": scope, "source": "backtest", "generated_at": payload.get("generated_at")}
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def comparable_stats(signal, min_samples=20):
    """Return live comparable stats, otherwise historical backtest stats."""
    rows = _read()
    signals = {r["id"]: r for r in rows if r.get("kind") == "signal" and r.get("id")}
    outcomes = {r["id"]: r for r in rows if r.get("kind") == "outcome" and r.get("id")}

    exact, fallback = [], []
    for sid, out in outcomes.items():
        s = signals.get(sid)
        if not s or out.get("outcome") not in ("WIN", "LOSS"):
            continue
        if (s.get("coin") == signal.get("coin") and
                s.get("interval") == signal.get("interval") and
                s.get("setup_type") == signal.get("setup_type")):
            exact.append(out["outcome"])
        if (s.get("interval") == signal.get("interval") and
                s.get("setup_type") == signal.get("setup_type")):
            fallback.append(out["outcome"])

    sample = exact if len(exact) >= min_samples else fallback
    scope = "exact" if len(exact) >= min_samples else "setup/timeframe"
    if sample:
        wins = sample.count("WIN")
        losses = sample.count("LOSS")
        selected_ids = []
        for sid, out in outcomes.items():
            s = signals.get(sid)
            if not s or out.get("outcome") not in ("WIN", "LOSS"):
                continue
            if (s.get("coin") == signal.get("coin") and
                    s.get("interval") == signal.get("interval") and
                    s.get("setup_type") == signal.get("setup_type")):
                selected_ids.append(sid)
            elif len(exact) < min_samples and (
                    s.get("interval") == signal.get("interval") and
                    s.get("setup_type") == signal.get("setup_type")):
                selected_ids.append(sid)
        chosen = [outcomes[sid] for sid in selected_ids if sid in outcomes]
        def avg(key, default=0.0):
            vals = [float(x[key]) for x in chosen if x.get(key) is not None]
            return sum(vals) / len(vals) if vals else default
        milestone_rates = {}
        for milestone in (5, 10, 20, 30, 50, 80):
            vals = [x.get("milestones", {}).get(str(milestone), False) for x in chosen]
            milestone_rates[str(milestone)] = sum(bool(v) for v in vals) / len(vals) * 100 if vals else 0.0
        return {"win_pct": wins / len(sample) * 100, "wins": wins,
                "losses": losses, "sample": len(sample), "scope": scope, "source": "live",
                "avg_mfe_pct": avg("mfe_pct"), "avg_mae_pct": avg("mae_pct"),
                "milestone_rates": milestone_rates}

    # Do not mix simulated/backtested outcomes into the live outcome log.
    # They are exposed separately so the alert remains transparent.
    historical = _backtest_stats(signal)
    if historical and historical["sample"] >= min_samples:
        return historical
    return {"win_pct": None, "wins": 0, "losses": 0, "sample": 0,
            "scope": scope}


def backfill_from_backtest(results):
    """Import completed backtest trades into live history when explicitly requested."""
    for result in results or []:
        for trade in result.get("trades", []):
            signal = {"coin": result.get("symbol"), "interval": result.get("interval"),
                      "setup_type": trade.get("setup_type"), "entry": trade.get("entry"),
                      "score": trade.get("score", 0), "potential_pct": trade.get("potential_pct", 0),
                      "rr": trade.get("rr", 0), "stop": trade.get("stop", 0),
                      "target": trade.get("target", 0), "trend_4h": trade.get("trend_4h")}
            record_signal(signal)
            record_outcome(signal, trade.get("outcome"), trade.get("exit_price"))
