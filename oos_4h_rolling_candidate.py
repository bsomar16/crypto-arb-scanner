#!/usr/bin/env python3
"""Research-only rolling OOS validation of the 4h low-extension/order-block candidate.

Candidate under test:
  entry_extension_pct <= 2% AND bullish order block

No production rules are changed. Each window is evaluated independently on the
same replay engine, then pooled only for descriptive evidence.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]
INTERVAL = "4h"
WINDOW_BARS = 750
WINDOWS = [
    ("oos_1", 750, 1500),
    ("oos_2", 1500, 2250),
    ("oos_3", 2250, 3000),
]


def is_candidate(row: dict) -> bool:
    return (
        float(row.get("entry_extension_pct", 999)) <= 2.0
        and bool((row.get("order_block") or {}).get("bullish"))
    )


def stats(trades: list[dict]) -> dict:
    s = summarize(trades)
    return {
        "signals": s["signals"],
        "closed": s["closed"],
        "wins": s["wins"],
        "losses": s["losses"],
        "precision_pct": round(s["precision_pct"], 2)
        if s["precision_pct"] is not None else None,
        "avg_mfe_pct": round(s["avg_mfe_pct"], 3)
        if s["avg_mfe_pct"] is not None else None,
        "avg_mae_pct": round(s["avg_mae_pct"], 3)
        if s["avg_mae_pct"] is not None else None,
    }


def main() -> None:
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)

    window_reports = []
    pooled_baseline: list[dict] = []
    pooled_candidate: list[dict] = []

    for window_name, start, end in WINDOWS:
        baseline_window: list[dict] = []
        candidate_window: list[dict] = []
        symbols_report = {}

        for symbol in SYMBOLS:
            rows = fetch_history(
                symbol, INTERVAL, int(cfg.get("backtest_bars", 3000))
            )
            trend_rows = fetch_history(
                symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8)
            )
            if len(rows) < end:
                continue

            audit = SignalAudit()
            trades = replay_symbol(
                symbol,
                INTERVAL,
                rows,
                trend_rows,
                start,
                end,
                cfg=deepcopy(cfg),
                audit=audit,
                entry_policy={
                    "require_retest": True,
                    "require_sweep": False,
                    "allow_early_retest": False,
                },
            )
            candidate = [t for t in trades if is_candidate(t)]
            baseline_window.extend(trades)
            candidate_window.extend(candidate)
            symbols_report[symbol] = {
                "baseline": stats(trades),
                "candidate": stats(candidate),
            }

        pooled_baseline.extend(baseline_window)
        pooled_candidate.extend(candidate_window)
        window_reports.append(
            {
                "window": window_name,
                "start_bar": start,
                "end_bar": end,
                "bars": end - start,
                "baseline": stats(baseline_window),
                "candidate": stats(candidate_window),
                "symbols": symbols_report,
            }
        )

    pooled = {
        "baseline": stats(pooled_baseline),
        "candidate": stats(pooled_candidate),
    }
    candidate_closed = pooled["candidate"]["closed"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "method": "current live-engine 4h replay across three rolling 750-bar OOS windows",
        "candidate": "entry_extension_pct <= 2% AND bullish order block",
        "warning": "Research evidence only. A candidate is not production-approved unless it survives independent OOS/forward validation with adequate sample size.",
        "minimum_closed_trade_sample_for_promotion": 20,
        "windows": window_reports,
        "pooled": pooled,
        "pooled_candidate_sample_met_20_closed": candidate_closed >= 20,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    with open("state/oos_4h_rolling_candidate.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
