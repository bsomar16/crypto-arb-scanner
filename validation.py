#!/usr/bin/env python3
"""Profile-aware historical validation for BUY signal recall/precision.

This is a measurement tool, not a trading gate. It evaluates each timeframe
with the same minimum score/volume/R:R profile used by the live signal engine.
Results are historical/out-of-sample evidence only; they are never a guarantee.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from statistics import mean

import backtest
from signals import STRATEGY_PROFILES

PATH = "state/profile_validation.json"


def _profile(interval):
    return next(p for p in STRATEGY_PROFILES.values() if p["interval"] == interval)


def summarize(rows):
    closed = [r for r in rows if r.get("outcome") in ("WIN", "LOSS")]
    wins = sum(r.get("outcome") == "WIN" for r in closed)
    losses = len(closed) - wins
    signals = len(rows)
    precision = wins / len(closed) * 100.0 if closed else None
    milestone_5 = sum(bool((r.get("milestones") or {}).get("5")) for r in rows)
    return {
        "signals": signals,
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "expired": sum(r.get("outcome") == "EXPIRED" for r in rows),
        "precision_pct": precision,
        "milestone_5_pct": milestone_5 / signals * 100.0 if signals else None,
        "avg_potential_pct": mean(float(r["potential_pct"]) for r in rows) if rows else None,
        "avg_rr": mean(float(r["rr"]) for r in rows) if rows else None,
    }


def run(cfg=None):
    cfg = cfg or {}
    symbols = [str(x).upper() for x in (cfg.get("backtest_symbols") or ["BTC", "ETH", "SOL"])]
    intervals = [x for x in (cfg.get("backtest_intervals") or ("5m", "15m", "1h"))
                 if x in {p["interval"] for p in STRATEGY_PROFILES.values()}]
    bars = int(cfg.get("backtest_bars", 3000))
    results = []
    for symbol in symbols:
        for interval in intervals:
            p = _profile(interval)
            result = backtest.run_symbol(
                symbol, interval=interval, bars=bars,
                min_vol_x=float(p["min_vol_x"]),
                min_potential_pct=float(cfg.get("signal_min_potential_pct", 5.0)),
                max_potential_pct=float(cfg.get("signal_max_potential_pct", 80.0)),
                min_score=float(p["min_score"]),
                min_rr=float(p["min_rr"]),
            )
            if result:
                summary = summarize(result.get("trades", []))
                results.append({
                    "symbol": symbol,
                    "interval": interval,
                    "profile": dict(p),
                    **summary,
                })

    by_interval = {}
    for interval in intervals:
        rows = [r for r in results if r["interval"] == interval]
        closed = sum(r["closed"] for r in rows)
        wins = sum(r["wins"] for r in rows)
        by_interval[interval] = {
            "signals": sum(r["signals"] for r in rows),
            "closed": closed,
            "wins": wins,
            "losses": sum(r["losses"] for r in rows),
            "precision_pct": wins / closed * 100.0 if closed else None,
            "min_sample_met": closed >= int(cfg.get("validation_min_closed_samples", 20)),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "profile-aware historical backtest",
        "warning": "Historical precision is descriptive evidence, not a guarantee of future performance.",
        "target_precision_pct": float(cfg.get("validation_target_precision_pct", 80.0)),
        "results": results,
        "by_interval": by_interval,
    }
    path = str(cfg.get("validation_stats_path", PATH))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def format_report(report):
    lines = ["PROFILE VALIDATION", f"Generated: {report.get('generated_at')}"]
    lines.append("Historical evidence only; no future-performance guarantee.")
    for interval, s in report.get("by_interval", {}).items():
        precision = "-" if s["precision_pct"] is None else f"{s['precision_pct']:.1f}%"
        lines.append(
            f"{interval}: signals={s['signals']} closed={s['closed']} "
            f"W/L={s['wins']}/{s['losses']} precision={precision} "
            f"sample_met={s['min_sample_met']}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(run()))
