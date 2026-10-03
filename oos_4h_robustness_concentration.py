#!/usr/bin/env python3
"""Research-only 4h OOS robustness study by symbol and rolling window.

No production rules are changed. The purpose is to detect whether promising
structural candidates are stable across assets or are concentrated in a small
subset of symbols/windows.
"""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]
INTERVAL = "4h"
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]
CANDIDATES = {
    "baseline": lambda r: True,
    "structure_ge_50": lambda r: float(r.get("structure_score", 0)) >= 50,
    "expansion_base": lambda r: str(r.get("expansion_state") or "") == "BASE",
    "structure_ge_50_and_expansion_base": lambda r: (
        float(r.get("structure_score", 0)) >= 50
        and str(r.get("expansion_state") or "") == "BASE"
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
    pooled = {c: [] for c in CANDIDATES}

    for name, start, end in WINDOWS:
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

        item = {"window": name, "start_bar": start, "end_bar": end, "symbols": {}}
        for symbol, trades in rows_by_symbol.items():
            base = metrics(trades)
            item["symbols"][symbol] = {"baseline": base, "candidates": {}}
            for cname, pred in CANDIDATES.items():
                selected = [r for r in trades if pred(r)]
                pooled[cname].extend(selected)
                z = metrics(selected)
                z["coverage_pct"] = round(100 * len(selected) / len(trades), 2) if trades else None
                z["delta_precision_pp"] = (
                    round(z["precision_pct"] - base["precision_pct"], 2)
                    if z["precision_pct"] is not None and base["precision_pct"] is not None
                    else None
                )
                item["symbols"][symbol]["candidates"][cname] = z
        windows.append(item)

    pooled_out = {}
    for cname, rows in pooled.items():
        pooled_out[cname] = metrics(rows)
        pooled_out[cname]["sample_met_20_closed"] = pooled_out[cname]["closed"] >= 20

    # Count independent symbol-window cells with >=5 closed trades and
    # positive precision delta versus that cell's baseline.
    stability = {}
    for cname in CANDIDATES:
        cells = []
        for w in windows:
            for symbol, data in w["symbols"].items():
                z = data["candidates"][cname]
                b = data["baseline"]
                if z["closed"] >= 5 and z["precision_pct"] is not None and b["precision_pct"] is not None:
                    cells.append({
                        "window": w["window"],
                        "symbol": symbol,
                        "closed": z["closed"],
                        "delta_precision_pp": z["delta_precision_pp"],
                        "improved": z["delta_precision_pp"] > 0,
                    })
        stability[cname] = {
            "eligible_cells": len(cells),
            "improved_cells": sum(c["improved"] for c in cells),
            "improvement_rate_pct": round(
                100 * sum(c["improved"] for c in cells) / len(cells), 2
            ) if cells else None,
            "cells": cells,
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "method": "current live-engine 4h replay, decomposed by symbol and rolling OOS window",
        "candidates": list(CANDIDATES),
        "windows": windows,
        "pooled": pooled_out,
        "stability": stability,
        "promotion_rule": (
            "Research only. A candidate should not be promoted from pooled precision alone; "
            "it must demonstrate stability across independent windows/assets and then pass "
            "a fresh forward/shadow validation period."
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_robustness_concentration.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
