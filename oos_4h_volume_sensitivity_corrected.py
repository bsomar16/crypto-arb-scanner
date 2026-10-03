#!/usr/bin/env python3
"""Research-only corrected 4h volume-floor sensitivity study.

The earlier sensitivity study passed min_vol_x overrides, but the production
signal engine still applied the hard-coded timeframe profile inside its volume
gate. This study runs only after the research-mode override is honored, and
compares frozen 4h volume floors across rolling OOS windows.

No production threshold is changed by this file.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ",
]
INTERVAL = "4h"
WINDOWS = [
    ("oos_1", 500, 1000),
    ("oos_2", 1000, 1500),
    ("oos_3", 1500, 2000),
    ("oos_4", 2000, 2500),
]
VOLUME_FLOORS = [0.95, 0.90, 0.85, 0.80]


def stats(rows):
    x = summarize(rows)
    return {
        "signals": x["signals"],
        "closed_decisive": x["closed"],
        "wins": x["wins"],
        "losses": x["losses"],
        "precision_pct": round(x["precision_pct"], 2) if x["precision_pct"] is not None else None,
        "avg_return_pct": round(x["avg_return_pct"], 3) if x.get("avg_return_pct") is not None else None,
        "avg_mfe_pct": round(x["avg_mfe_pct"], 3) if x.get("avg_mfe_pct") is not None else None,
        "avg_mae_pct": round(x["avg_mae_pct"], 3) if x.get("avg_mae_pct") is not None else None,
        "avg_hold_hours": round(x["avg_hold_hours"], 2) if x.get("avg_hold_hours") is not None else None,
    }


def replay_floor(symbol, rows, trend, start, end, cfg, floor):
    replay_cfg = deepcopy(cfg)
    replay_cfg["sensitivity_mode"] = True
    audit = SignalAudit()
    return replay_symbol(
        symbol,
        INTERVAL,
        rows,
        trend,
        start,
        end,
        cfg=replay_cfg,
        audit=audit,
        entry_policy={
            "require_retest": True,
            "require_sweep": False,
            "allow_early_retest": False,
        },
        min_vol_x=floor,
    )


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    report = []
    pooled = {f"vol_{floor:.2f}": [] for floor in VOLUME_FLOORS}

    for name, start, end in WINDOWS:
        by_floor = {f"vol_{floor:.2f}": [] for floor in VOLUME_FLOORS}
        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(
                symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8)
            )
            if len(rows) < end:
                continue
            for floor in VOLUME_FLOORS:
                by_floor[f"vol_{floor:.2f}"].extend(
                    replay_floor(symbol, rows, trend, start, end, cfg, floor)
                )

        baseline = stats(by_floor["vol_0.95"])
        item = {
            "window": name,
            "start_bar": start,
            "end_bar": end,
            "baseline": baseline,
            "variants": {},
        }

        for key, rows in by_floor.items():
            pooled[key].extend(rows)
            z = stats(rows)
            z["coverage_pct"] = (
                round(100.0 * len(rows) / max(len(by_floor["vol_0.95"]), 1), 2)
            )
            if z["precision_pct"] is not None and baseline["precision_pct"] is not None:
                z["delta_precision_pp"] = round(
                    z["precision_pct"] - baseline["precision_pct"], 2
                )
            else:
                z["delta_precision_pp"] = None
            z["sample_met_20_closed"] = z["closed_decisive"] >= 20
            item["variants"][key] = z

        report.append(item)

    pooled_out = {}
    for key, rows in pooled.items():
        z = stats(rows)
        z["sample_met_20_closed"] = z["closed_decisive"] >= 20
        baseline = pooled["vol_0.95"]
        base_stats = stats(baseline)
        z["delta_precision_pp"] = (
            round(z["precision_pct"] - base_stats["precision_pct"], 2)
            if z["precision_pct"] is not None and base_stats["precision_pct"] is not None
            else None
        )
        informative = [
            w["variants"][key]
            for w in report
            if w["variants"][key]["closed_decisive"] > 0
            and w["baseline"]["closed_decisive"] > 0
        ]
        z["stable_across_informative_windows"] = bool(informative) and all(
            v["delta_precision_pp"] >= 0 for v in informative
        )
        z["research_eligible"] = (
            z["sample_met_20_closed"] and z["stable_across_informative_windows"]
        )
        pooled_out[key] = z

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "method": "current live-engine 4h replay across four sequential 500-bar OOS windows",
        "purpose": "Correctly measure 4h volume-floor sensitivity after fixing the research override path",
        "volume_floors": VOLUME_FLOORS,
        "windows": report,
        "pooled": pooled_out,
        "warning": "Research-only. No production BUY rule or timeframe profile is changed.",
        "promotion_rule": "A relaxed volume floor must preserve/improve precision in every informative independent window, reach >=20 decisive pooled outcomes, and then pass fresh forward/shadow validation before production consideration.",
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_volume_sensitivity_corrected.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
