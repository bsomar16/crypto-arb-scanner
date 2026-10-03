#!/usr/bin/env python3
"""Research-only 4h candidate x exit-policy asset-holdout study.

The production signal engine and candidate definitions are frozen. The study
evaluates predeclared entry candidates on assets excluded from discovery under
TP1/TP2/TP3 exit policies. No production rule is changed.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history
from signal_audit import SignalAudit
from oos_4h_exit_policy import collect_signals, resolve_exit, summarize

SYMBOLS = ["LTC", "LINK", "DOT", "AVAX", "TRX", "SUI", "TON"]
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]
CANDIDATES = {
    "baseline": lambda r: True,
    "structure_ge_50": lambda r: float(r.get("structure_score", 0)) >= 50,
    "base_and_structure_ge_50": lambda r: (
        float(r.get("structure_score", 0)) >= 50
        and str(r.get("expansion_state") or "") == "BASE"
    ),
    "non_expansion_and_structure_ge_50": lambda r: (
        str(r.get("expansion_state") or "") != "EXPANSION"
        and float(r.get("structure_score", 0)) >= 50
    ),
}
POLICIES = ("TP1", "TP2", "TP3")


def compact(rows):
    x = summarize(rows)
    return {
        "signals": x["signals"],
        "closed_decisive": x["closed_decisive"],
        "wins": x["wins"],
        "losses": x["losses"],
        "precision_pct": x["precision_pct"],
        "avg_return_pct": x["avg_return_pct"],
        "sample_met_20_closed": x["closed_decisive"] >= 20,
    }


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    windows = []
    pooled = {(candidate, policy): [] for candidate in CANDIDATES for policy in POLICIES}

    for window_name, start, end in WINDOWS:
        collected = {candidate: {policy: [] for policy in POLICIES} for candidate in CANDIDATES}
        base_signal_count = 0

        for symbol in SYMBOLS:
            rows = fetch_history(symbol, "4h", int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue

            signals = collect_signals(symbol, rows, trend, start, end, cfg)
            base_signal_count += len(signals)

            for signal_index, signal in signals:
                for candidate, predicate in CANDIDATES.items():
                    if not predicate(signal):
                        continue
                    for policy in POLICIES:
                        result = resolve_exit(rows, signal_index, signal, policy, end)
                        if result is None:
                            continue
                        result.update({"symbol": symbol, "signal_index": signal_index})
                        collected[candidate][policy].append(result)
                        pooled[(candidate, policy)].append(result)

        item = {
            "window": window_name,
            "start_bar": start,
            "end_bar": end,
            "baseline_signals": base_signal_count,
            "candidates": {},
        }
        baseline_by_policy = {
            policy: compact(collected["baseline"][policy]) for policy in POLICIES
        }
        for candidate in CANDIDATES:
            item["candidates"][candidate] = {}
            for policy in POLICIES:
                metrics = compact(collected[candidate][policy])
                base_precision = baseline_by_policy[policy]["precision_pct"]
                metrics["delta_vs_baseline_pp"] = (
                    round(metrics["precision_pct"] - base_precision, 2)
                    if metrics["precision_pct"] is not None and base_precision is not None
                    else None
                )
                item["candidates"][candidate][policy] = metrics
        windows.append(item)

    pooled_out = {}
    for candidate in CANDIDATES:
        pooled_out[candidate] = {}
        for policy in POLICIES:
            metrics = compact(pooled[(candidate, policy)])
            base_precision = compact(pooled[("baseline", policy)])["precision_pct"]
            metrics["delta_vs_baseline_pp"] = (
                round(metrics["precision_pct"] - base_precision, 2)
                if metrics["precision_pct"] is not None and base_precision is not None
                else None
            )
            pooled_out[candidate][policy] = metrics

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "holdout_assets": SYMBOLS,
        "method": "frozen 4h signal candidates evaluated on asset holdout under frozen TP1/TP2/TP3 exit policies",
        "candidates": list(CANDIDATES),
        "exit_policies": list(POLICIES),
        "windows": windows,
        "pooled": pooled_out,
        "promotion_rule": (
            "A candidate/exit combination is research-eligible only after stable "
            "holdout precision, >=20 decisive pooled outcomes, and fresh forward/shadow validation. "
            "No combination is promoted by this study."
        ),
        "warning": "Research-only. No production BUY, entry, target, stop, or execution rule is changed.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_candidate_exit_holdout.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
