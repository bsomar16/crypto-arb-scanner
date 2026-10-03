#!/usr/bin/env python3
"""Research-only five-window 4h TP1 holdout study.

The production 4h signal engine is frozen. This study evaluates the
predeclared entry candidates under a single frozen TP1 exit policy across
five sequential OOS windows and the same 20-symbol SPOT universe.
No production rule is changed and no threshold search is performed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from live_replay import fetch_history
from oos_4h_exit_policy import collect_signals, resolve_exit, summarize

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ",
]
INTERVAL = "4h"
# Five independent 500-bar segments cover the same 2,500-bar research span.
WINDOWS = [
    ("oos_1", 500, 1000),
    ("oos_2", 1000, 1500),
    ("oos_3", 1500, 2000),
    ("oos_4", 2000, 2500),
    ("oos_5", 2500, 3000),
]
CANDIDATES = {
    "baseline": lambda r: True,
    "structure_ge_50": lambda r: float(r.get("structure_score", 0) or 0) >= 50.0,
    "base_and_structure_ge_50": lambda r: (
        float(r.get("structure_score", 0) or 0) >= 50.0
        and str(r.get("expansion_state") or "") == "BASE"
    ),
    "non_expansion_and_structure_ge_50": lambda r: (
        str(r.get("expansion_state") or "") != "EXPANSION"
        and float(r.get("structure_score", 0) or 0) >= 50.0
    ),
}
POLICY = "TP1"


def main() -> None:
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)

    pooled = {name: [] for name in CANDIDATES}
    windows = []

    for window_name, start, end in WINDOWS:
        collected = {name: [] for name in CANDIDATES}
        baseline_signals = 0

        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(
                symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8),
            )
            if len(rows) < end:
                continue

            signals = collect_signals(symbol, rows, trend, start, end, cfg)
            baseline_signals += len(signals)

            for signal_index, signal in signals:
                for candidate, predicate in CANDIDATES.items():
                    if not predicate(signal):
                        continue
                    result = resolve_exit(rows, signal_index, signal, POLICY, end)
                    if result is None:
                        continue
                    result.update({"symbol": symbol, "signal_index": signal_index})
                    collected[candidate].append(result)
                    pooled[candidate].append(result)

        baseline_metrics = summarize(collected["baseline"])
        item = {
            "window": window_name,
            "start_bar": start,
            "end_bar": end,
            "baseline_signals": baseline_signals,
            "candidates": {},
        }

        for candidate, rows in collected.items():
            metrics = summarize(rows)
            baseline_precision = baseline_metrics["precision_pct"]
            metrics["sample_met_20_closed"] = metrics["closed_decisive"] >= 20
            metrics["delta_vs_baseline_pp"] = (
                round(metrics["precision_pct"] - baseline_precision, 2)
                if metrics["precision_pct"] is not None
                and baseline_precision is not None
                else None
            )
            item["candidates"][candidate] = metrics

        windows.append(item)

    pooled_out = {}
    baseline_pooled = summarize(pooled["baseline"])
    for candidate, rows in pooled.items():
        metrics = summarize(rows)
        metrics["sample_met_20_closed"] = metrics["closed_decisive"] >= 20
        base_precision = baseline_pooled["precision_pct"]
        metrics["delta_vs_baseline_pp"] = (
            round(metrics["precision_pct"] - base_precision, 2)
            if metrics["precision_pct"] is not None and base_precision is not None
            else None
        )
        pooled_out[candidate] = metrics

    informative_windows = [
        w for w in windows if w["candidates"]["baseline"]["closed_decisive"] > 0
    ]
    for candidate in CANDIDATES:
        candidate_rows = [w["candidates"][candidate] for w in informative_windows]
        # A candidate is stable only if it does not fall below the same-window
        # baseline in any informative window and has >=20 pooled decisive outcomes.
        stable = bool(candidate_rows) and all(
            c["delta_vs_baseline_pp"] is not None and c["delta_vs_baseline_pp"] >= 0
            for c in candidate_rows
        )
        pooled_out[candidate]["stable_across_informative_windows"] = stable
        pooled_out[candidate]["research_eligible"] = (
            stable and pooled_out[candidate]["sample_met_20_closed"]
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "universe": SYMBOLS,
        "method": "current production 4h signal stream evaluated under frozen TP1 exit policy across five sequential 500-bar OOS windows",
        "policy": POLICY,
        "candidates": list(CANDIDATES),
        "windows": windows,
        "pooled": pooled_out,
        "promotion_rule": (
            "Candidate must preserve/improve same-window TP1 precision in every "
            "informative window, reach >=20 decisive pooled outcomes, then pass "
            "fresh forward/shadow validation. 90% remains a research target only."
        ),
        "warning": "Research-only. No production BUY, target, stop, or execution rule is changed.",
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_five_window_tp1_holdout.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
