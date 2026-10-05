#!/usr/bin/env python3
"""Research-only descriptive taxonomy of closed 4h BUY losses.

No thresholds are selected or changed. The classifier is deterministic and uses
only entry-time signal fields plus the realized post-entry path.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean

from live_replay import fetch_history, replay_symbol

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]
INTERVAL = "4h"
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]


def _f(row, key, default=0.0):
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def classify_loss(row):
    """Assign one descriptive failure mode, with deterministic precedence."""
    mfe = _f(row, "mfe_pct")
    mae = _f(row, "mae_pct")
    potential = max(_f(row, "potential_pct"), 0.0)
    rr = _f(row, "rr")
    hold = _f(row, "estimated_hold_hours")

    # These labels describe realized path, not predictive rules.
    if mfe < 1.0 and mae <= -1.0:
        return "immediate_failed_followthrough"
    if potential > 0 and mfe < 0.25 * potential and mae <= -1.5:
        return "low_followthrough_vs_target"
    if rr > 0 and mfe >= max(2.0, 0.5 * potential) and mae <= -1.5:
        return "late_reversal_after_progress"
    if mfe >= 5.0 and mae <= -2.0:
        return "high_mfe_but_stop_failure"
    if hold <= 8 and mae <= -1.5:
        return "fast_stopout"
    return "other_loss"


def _summary(rows):
    losses = [r for r in rows if r.get("outcome") == "LOSS"]
    out = {
        "losses": len(losses),
        "avg_mfe_pct": round(mean(_f(r, "mfe_pct") for r in losses), 3) if losses else None,
        "avg_mae_pct": round(mean(_f(r, "mae_pct") for r in losses), 3) if losses else None,
        "by_failure_mode": {},
        "by_setup": {},
        "by_trend": {},
        "by_expansion_state": {},
        "by_structure_band": {},
    }
    for r in losses:
        mode = classify_loss(r)
        out["by_failure_mode"].setdefault(mode, {"count": 0, "share_pct": 0.0, "loss_mae_sum": 0.0})
        out["by_failure_mode"][mode]["count"] += 1
        out["by_failure_mode"][mode]["loss_mae_sum"] += _f(r, "mae_pct")

        setup = str(r.get("setup") or "UNKNOWN")
        out["by_setup"].setdefault(setup, 0)
        out["by_setup"][setup] += 1

        trend = str(r.get("trend_4h") or "UNKNOWN")
        out["by_trend"].setdefault(trend, 0)
        out["by_trend"][trend] += 1

        expansion = str(r.get("expansion_state") or "UNKNOWN")
        out["by_expansion_state"].setdefault(expansion, 0)
        out["by_expansion_state"][expansion] += 1

        structure = _f(r, "structure_score", 0.0)
        band = "0-49" if structure < 50 else ("50-64" if structure < 65 else "65+")
        out["by_structure_band"].setdefault(band, 0)
        out["by_structure_band"][band] += 1

    if losses:
        for item in out["by_failure_mode"].values():
            item["share_pct"] = round(item["count"] / len(losses) * 100, 2)
            item["loss_mae_sum"] = round(item["loss_mae_sum"], 3)
    return out


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    windows = []
    pooled = []
    for name, start, end in WINDOWS:
        trades = []
        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue
            trades += replay_symbol(
                symbol,
                INTERVAL,
                rows,
                trend,
                start,
                end,
                cfg=deepcopy(cfg),
                entry_policy={
                    "require_retest": True,
                    "require_sweep": False,
                    "allow_early_retest": False,
                },
            )
        closed_losses = [r for r in trades if r.get("outcome") == "LOSS"]
        for r in closed_losses:
            pooled.append(r)
        windows.append({
            "window": name,
            "start_bar": start,
            "end_bar": end,
            "signals": len(trades),
            "closed_losses": len(closed_losses),
            "summary": _summary(trades),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "method": "Current production 4h replay; descriptive loss taxonomy only; no threshold selection.",
        "windows": windows,
        "pooled": _summary(pooled),
        "interpretation_guardrail": (
            "Failure modes are descriptive hypotheses only. Do not promote a mode to a live "
            "filter without a fresh predeclared OOS test and rolling validation."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    with open("state/oos_4h_loss_taxonomy.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
