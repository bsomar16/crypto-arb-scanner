#!/usr/bin/env python3
"""Research-only asset-holdout validation of the predeclared 4h structure>=50 candidate."""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

# These assets were deliberately not used by the discovery study (PR #99).
HOLDOUT_SYMBOLS = ["LTC", "LINK", "DOT", "AVAX", "TRX", "SUI", "TON"]
INTERVAL = "4h"
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]

def summary(rows):
    x = summarize(rows)
    return {
        "signals": x["signals"],
        "closed": x["closed"],
        "wins": x["wins"],
        "losses": x["losses"],
        "precision_pct": round(x["precision_pct"], 2) if x["precision_pct"] is not None else None,
        "avg_mfe_pct": round(x["avg_mfe_pct"], 3) if x["avg_mfe_pct"] is not None else None,
        "avg_mae_pct": round(x["avg_mae_pct"], 3) if x["avg_mae_pct"] is not None else None,
    }

def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    windows = []
    pooled_base = []
    pooled_candidate = []

    for name, start, end in WINDOWS:
        base_rows = []
        candidate_rows = []

        for symbol in HOLDOUT_SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue

            audit = SignalAudit()
            trades = replay_symbol(
                symbol, INTERVAL, rows, trend, start, end,
                cfg=deepcopy(cfg), audit=audit,
                entry_policy={
                    "require_retest": True,
                    "require_sweep": False,
                    "allow_early_retest": False,
                },
            )
            base_rows.extend(trades)
            candidate_rows.extend(
                r for r in trades if float(r.get("structure_score", 0)) >= 50
            )

        base = summary(base_rows)
        candidate = summary(candidate_rows)
        candidate["coverage_pct"] = round(100 * len(candidate_rows) / len(base_rows), 2) if base_rows else None
        candidate["delta_precision_pp"] = (
            round(candidate["precision_pct"] - base["precision_pct"], 2)
            if candidate["precision_pct"] is not None and base["precision_pct"] is not None
            else None
        )
        candidate["sample_met_20_closed"] = candidate["closed"] >= 20
        windows.append({
            "window": name,
            "start_bar": start,
            "end_bar": end,
            "baseline": base,
            "structure_ge_50": candidate,
        })
        pooled_base.extend(base_rows)
        pooled_candidate.extend(candidate_rows)

    pooled = {
        "baseline": summary(pooled_base),
        "structure_ge_50": summary(pooled_candidate),
    }
    pooled["structure_ge_50"]["coverage_pct"] = (
        round(100 * len(pooled_candidate) / len(pooled_base), 2) if pooled_base else None
    )
    pooled["structure_ge_50"]["delta_precision_pp"] = (
        round(
            pooled["structure_ge_50"]["precision_pct"] - pooled["baseline"]["precision_pct"],
            2,
        )
        if pooled["structure_ge_50"]["precision_pct"] is not None
        and pooled["baseline"]["precision_pct"] is not None
        else None
    )
    pooled["structure_ge_50"]["sample_met_20_closed"] = pooled["structure_ge_50"]["closed"] >= 20

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "holdout_assets": HOLDOUT_SYMBOLS,
        "method": "4h current-engine replay across three rolling OOS windows on assets excluded from candidate discovery",
        "candidate": "structure_score >= 50",
        "windows": windows,
        "pooled": pooled,
        "warning": "Research-only. No production rule is changed from these results.",
        "promotion_rule": "Do not promote unless the candidate generalizes to holdout assets, preserves precision across non-empty windows, and reaches an adequate closed-trade sample before forward/shadow validation.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_structure_holdout.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
