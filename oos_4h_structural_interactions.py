#!/usr/bin/env python3
"""Research-only rolling OOS validation of predefined 4h structural/setup/regime interactions."""
# CI trigger: run the dedicated five-window workflow on the next main push.
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = [
    "BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT",
    "LTC","BCH","UNI","NEAR","ATOM","APT","ARB","OP","SUI","INJ",
]
INTERVAL = "4h"
WINDOWS = [
    ("oos_1", 500, 1000),
    ("oos_2", 1000, 1500),
    ("oos_3", 1500, 2000),
    ("oos_4", 2000, 2500),
    ("oos_5", 2500, 3000),
]

def structure(r):
    return float(r.get("structure_score", 0))

def setup(r):
    # Canonical live-engine field is setup_type; keep setup as a legacy fallback
    # so historical rows produced by older replay code remain analyzable.
    return str(r.get("setup_type") or r.get("setup") or "").upper()

def expansion(r):
    return str(r.get("expansion_state") or "").upper()

CANDIDATES = {
    "baseline": lambda r: True,
    "structure_ge_50": lambda r: structure(r) >= 50,
    "pullback": lambda r: setup(r) == "PULLBACK",
    "base": lambda r: expansion(r) == "BASE",
    "structure_ge_50_and_pullback": lambda r: structure(r) >= 50 and setup(r) == "PULLBACK",
    "structure_ge_50_and_base": lambda r: structure(r) >= 50 and expansion(r) == "BASE",
    "pullback_and_base": lambda r: setup(r) == "PULLBACK" and expansion(r) == "BASE",
    "structure_ge_50_pullback_and_base": lambda r: (
        structure(r) >= 50 and setup(r) == "PULLBACK" and expansion(r) == "BASE"
    ),
}

def summarize_rows(rows):
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

    report = []
    pooled = {name: [] for name in CANDIDATES}

    for window_name, start, end in WINDOWS:
        trades = []
        for symbol in SYMBOLS:
            rows = fetch_history(
                symbol, INTERVAL, max(4500, int(cfg.get("backtest_bars", 4500)))
            )
            trend = fetch_history(
                symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8)
            )
            if len(rows) < end:
                continue
            audit = SignalAudit()
            trades += replay_symbol(
                symbol,
                INTERVAL,
                rows,
                trend,
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

        baseline = summarize_rows(trades)
        item = {
            "window": window_name,
            "start_bar": start,
            "end_bar": end,
            "baseline": baseline,
            "candidates": {},
        }

        for name, predicate in CANDIDATES.items():
            selected = [row for row in trades if predicate(row)]
            pooled[name].extend(selected)
            result = summarize_rows(selected)
            result["coverage_pct"] = round(100 * len(selected) / len(trades), 2) if trades else None
            result["delta_precision_pp"] = (
                round(result["precision_pct"] - baseline["precision_pct"], 2)
                if result["precision_pct"] is not None and baseline["precision_pct"] is not None
                else None
            )
            result["sample_met_20_closed"] = result["closed"] >= 20
            item["candidates"][name] = result

        report.append(item)

    pooled_out = {}
    for name, rows in pooled.items():
        result = summarize_rows(rows)
        result["sample_met_20_closed"] = result["closed"] >= 20
        pooled_out[name] = result

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "method": "current live-engine 4h replay across five sequential 500-bar OOS windows",
        "research_universe": SYMBOLS,
        "candidates": list(CANDIDATES.keys()),
        "windows": report,
        "pooled": pooled_out,
        "warning": "Research-only. No production rule is changed from these results.",
        "promotion_rule": (
            "A candidate must improve or preserve precision across independent windows, "
            "retain an adequate closed-trade sample, and then pass forward/shadow validation "
            "before any production consideration."
        ),
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_structural_interactions.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
