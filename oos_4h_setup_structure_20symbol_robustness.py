#!/usr/bin/env python3
"""Research-only 4h setup/structure robustness across 20 liquid spot symbols."""
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
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]

# Frozen before evaluation. No threshold search is performed.
CANDIDATES = {
    "baseline": lambda r: True,
    "pullback": lambda r: str(r.get("setup") or "") == "PULLBACK",
    "pullback_and_structure_ge_50": lambda r: (
        str(r.get("setup") or "") == "PULLBACK"
        and float(r.get("structure_score", 0)) >= 50
    ),
    "pullback_and_base": lambda r: (
        str(r.get("setup") or "") == "PULLBACK"
        and str(r.get("expansion_state") or "") == "BASE"
    ),
    "pullback_structure_and_base": lambda r: (
        str(r.get("setup") or "") == "PULLBACK"
        and float(r.get("structure_score", 0)) >= 50
        and str(r.get("expansion_state") or "") == "BASE"
    ),
    "non_expansion_and_structure_ge_50": lambda r: (
        str(r.get("expansion_state") or "") != "EXPANSION"
        and float(r.get("structure_score", 0)) >= 50
    ),
}

def metrics(rows):
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
    pooled = {name: [] for name in CANDIDATES}

    for window_name, start, end in WINDOWS:
        rows_by_symbol = {}
        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue
            audit = SignalAudit()
            rows_by_symbol[symbol] = replay_symbol(
                symbol, INTERVAL, rows, trend, start, end,
                cfg=deepcopy(cfg), audit=audit,
                entry_policy={
                    "require_retest": True,
                    "require_sweep": False,
                    "allow_early_retest": False,
                },
            )

        item = {"window": window_name, "start_bar": start, "end_bar": end, "symbols": {}}
        for symbol, trades in rows_by_symbol.items():
            baseline = metrics(trades)
            item["symbols"][symbol] = {"baseline": baseline, "candidates": {}}
            for candidate, predicate in CANDIDATES.items():
                selected = [r for r in trades if predicate(r)]
                pooled[candidate].extend(selected)
                result = metrics(selected)
                result["coverage_pct"] = round(100 * len(selected) / len(trades), 2) if trades else None
                result["delta_precision_pp"] = (
                    round(result["precision_pct"] - baseline["precision_pct"], 2)
                    if result["precision_pct"] is not None and baseline["precision_pct"] is not None
                    else None
                )
                result["sample_met_20_closed"] = result["closed"] >= 20
                item["symbols"][symbol]["candidates"][candidate] = result
        windows.append(item)

    pooled_out = {}
    for candidate, rows in pooled.items():
        pooled_out[candidate] = metrics(rows)
        pooled_out[candidate]["sample_met_20_closed"] = pooled_out[candidate]["closed"] >= 20

    stability = {}
    for candidate in CANDIDATES:
        cells = []
        for window in windows:
            for symbol, data in window["symbols"].items():
                result = data["candidates"][candidate]
                baseline = data["baseline"]
                if (
                    result["closed"] >= 5
                    and result["precision_pct"] is not None
                    and baseline["precision_pct"] is not None
                ):
                    cells.append({
                        "window": window["window"],
                        "symbol": symbol,
                        "closed": result["closed"],
                        "delta_precision_pp": result["delta_precision_pp"],
                        "improved": result["delta_precision_pp"] > 0,
                    })
        stability[candidate] = {
            "eligible_cells": len(cells),
            "improved_cells": sum(c["improved"] for c in cells),
            "improvement_rate_pct": (
                round(100 * sum(c["improved"] for c in cells) / len(cells), 2)
                if cells else None
            ),
            "cells": cells,
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_universe": SYMBOLS,
        "method": "current live-engine 4h replay across three rolling 750-bar OOS windows, decomposed by symbol",
        "hypothesis_policy": "frozen setup/structure hypotheses; no threshold search or production mutation",
        "candidates": list(CANDIDATES),
        "windows": windows,
        "pooled": pooled_out,
        "stability": stability,
        "promotion_rule": (
            "A candidate must improve or preserve precision across independent "
            "windows/assets, reach >=20 pooled closed trades, and then pass a "
            "fresh forward/shadow validation period before production consideration."
        ),
        "warning": "Research-only. No production BUY or execution rule is changed.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_setup_structure_20symbol_robustness.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
