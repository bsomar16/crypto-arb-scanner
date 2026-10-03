#!/usr/bin/env python3
"""Research-only diagnostics for current 4h BUY-signal flow.

This does not alter production thresholds or signal behavior. It replays the
current production 4h engine on the frozen 20-symbol SPOT universe and records
where candidates are rejected, plus the strongest causal near-misses.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history
from signal_audit import SignalAudit
from signals import intraday_signal

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ",
]
INTERVAL = "4h"
OUTPUT = "state/oos_4h_flow_rejection_diagnostics.json"


def main() -> None:
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)

    audit = SignalAudit(near_miss_limit=500)
    scanned = 0
    data_failures = 0

    for symbol in SYMBOLS:
        rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
        trend = fetch_history(
            symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8)
        )
        if len(rows) < 70 or not trend:
            data_failures += 1
            continue

        scanned += 1
        intraday_signal(
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
            cfg=deepcopy(cfg),
            historical_data=rows,
            historical_trend_data=trend,
            audit=audit,
            record_history=False,
        )

    report = audit.summary(scans=scanned, hits=0, fresh=0, selected=0)
    stages = report["stages"]
    rejected = max(0, report["rejected"])
    stage_rates = {
        stage: round(100.0 * count / rejected, 2) if rejected else 0.0
        for stage, count in stages.items()
        if stage != "qualified"
    }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "interval": INTERVAL,
        "universe": SYMBOLS,
        "symbols_scanned": scanned,
        "data_failures": data_failures,
        "production_cfg_snapshot": {
            "buy_fast_min_hour_vol": cfg.get("buy_fast_min_hour_vol"),
            "signal_min_potential_pct": cfg.get("signal_min_potential_pct"),
            "signal_max_potential_pct": cfg.get("signal_max_potential_pct"),
            "signal_min_score": cfg.get("signal_min_score"),
            "signal_min_rr": cfg.get("signal_min_rr"),
            "signal_require_retest": cfg.get("signal_require_retest"),
            "signal_require_sweep": cfg.get("signal_require_sweep"),
            "signal_allow_early_retest": cfg.get("signal_allow_early_retest"),
        },
        "summary": {
            "attempts": report["attempts"],
            "qualified": report["qualified"],
            "rejected": report["rejected"],
            "qualification_rate_pct": report["qualification_rate_pct"],
        },
        "rejection_stage_counts": stages,
        "rejection_stage_share_pct": stage_rates,
        "by_interval": report["by_interval"],
        "by_setup": report["by_setup"],
        "top_near_misses": report["near_misses"][:100],
        "interpretation_guardrail": (
            "Diagnostic only. Do not relax a production gate from this report alone. "
            "Any candidate policy must pass independent OOS, rolling validation, and "
            "forward/shadow validation before production consideration."
        ),
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
