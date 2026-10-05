#!/usr/bin/env python3
"""Research-only 4h baseline power study. Main baseline is frozen; no candidate filter search.

Uses a wider coin universe and longer pre-holdout history. Baseline only:
no new filter, no threshold search, no candidate selection.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean

from live_replay import fetch_history, replay_symbol

SYMBOLS = [
    "BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","TON",
    "TRX","DOT","LTC","AAVE","UNI","SUI","NEAR","TAO","FIL","ATOM",
]
INTERVAL = "4h"
BARS = 6000
HOLDOUT_START_MS = 1791244800000  # 2026-10-06 00:00 UTC
WINDOWS = [("w1", 2500, 3500), ("w2", 3500, 4500), ("w3", 4500, 5500)]


def wilson_lower(wins, closed):
    if closed <= 0:
        return None
    z = 1.959963984540054
    p = wins / closed
    return (p + z*z/(2*closed) - z*math.sqrt(p*(1-p)/closed + z*z/(4*closed*closed))) / (1 + z*z/closed)


def net_r(row, cfg):
    entry = float(row.get("entry", 0) or 0)
    stop = float(row.get("stop", 0) or 0)
    target = float(row.get("t3", row.get("target", entry)) or entry)
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None
    risk_pct = (entry - stop) / entry * 100.0
    fees = cfg.get("spot_taker_fee_pct") or {}
    fee = float(fees.get("binance", 0.0) or 0.0)
    slip = float(cfg.get("realtime_slippage_reserve_pct", 0.0) or 0.0)
    cost = 2.0 * (fee + slip)
    if row.get("outcome") == "WIN":
        return ((target - entry) / entry * 100.0 - cost) / risk_pct
    if row.get("outcome") == "LOSS":
        return (-risk_pct - cost) / risk_pct
    return None


def metrics(rows, cfg):
    closed = [r for r in rows if r.get("outcome") in ("WIN","LOSS")]
    wins = sum(r.get("outcome") == "WIN" for r in closed)
    rs = [x for x in (net_r(r, cfg) for r in closed) if x is not None]
    positives = [x for x in rs if x > 0]
    negatives = [x for x in rs if x <= 0]
    equity = peak = max_dd = 0.0
    streak = best_streak = 0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if x < 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
    return {
        "signals": len(rows),
        "closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "precision_pct": round(wins / len(closed) * 100.0, 2) if closed else None,
        "wilson_95_lower_pct": round(wilson_lower(wins, len(closed)) * 100.0, 2) if closed else None,
        "expectancy_r_after_configured_cost_scenario": round(mean(rs), 4) if rs else None,
        "profit_factor": round(sum(positives) / abs(sum(negatives)), 3) if positives and negatives else None,
        "average_win_r": round(mean(positives), 3) if positives else None,
        "average_loss_r": round(mean(negatives), 3) if negatives else None,
        "max_losing_streak": best_streak,
        "max_drawdown_r": round(max_dd, 3),
        "average_hold_hours": round(mean(float(r.get("estimated_hold_hours", 0)) for r in closed), 2) if closed else None,
        "average_mfe_pct": round(mean(float(r.get("mfe_pct", 0)) for r in closed), 3) if closed else None,
        "average_mae_pct": round(mean(float(r.get("mae_pct", 0)) for r in closed), 3) if closed else None,
    }


def main():
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)

    all_rows = {}
    for symbol in SYMBOLS:
        rows = fetch_history(symbol, INTERVAL, BARS)
        rows = [r for r in rows if int(r[0]) < HOLDOUT_START_MS]
        trend = [r for r in fetch_history(symbol, "1d", max(500, BARS // 8)) if int(r[0]) < HOLDOUT_START_MS]
        if len(rows) < WINDOWS[-1][2]:
            continue
        all_rows[symbol] = (rows, trend)

    windows = []
    pooled = []
    for name, start, end in WINDOWS:
        rows = []
        for symbol, (history, trend) in all_rows.items():
            batch = replay_symbol(
                symbol, INTERVAL, history, trend, start, end,
                cfg=deepcopy(cfg),
                entry_policy={"require_retest": True, "require_sweep": False, "allow_early_retest": False},
            )
            rows.extend(batch)
        pooled.extend(rows)
        windows.append({"window": name, "start_bar": start, "end_bar": end, "symbols": len(all_rows), "metrics": metrics(rows, cfg)})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "candidate": "baseline",
        "universe": SYMBOLS,
        "requested_bars": BARS,
        "holdout_start_utc": "2026-10-06T00:00:00Z",
        "windows": windows,
        "pooled": metrics(pooled, cfg),
        "sample_rule": "pooled closed >=100 and >=20 closed per window; Wilson 95% lower bound must clear cost-implied breakeven; no single-window lift accepted.",
        "cost_model_note": "Expectancy/R metrics use configured Binance taker fee and slippage reserve as a scenario, not realized execution costs.",
        "decision": "INSUFFICIENT SAMPLE" if metrics(pooled, cfg)["closed"] < 100 or any(x["metrics"]["closed"] < 20 for x in windows) else "RESEARCH ONLY — REVIEW PASS RULES",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    import os
    os.makedirs("state", exist_ok=True)
    with open("state/oos_4h_baseline_power.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
