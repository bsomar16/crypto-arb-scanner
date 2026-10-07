#!/usr/bin/env python3
"""Research-only 4h early-retest body-threshold OOS sensitivity study.

Production thresholds are unchanged. This study replays the frozen 4h signal
stream with early retest enabled and varies only its body-strength threshold.
Each candidate is evaluated across five sequential OOS windows under TP1/TP3.
"""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history
from oos_4h_exit_policy import resolve_exit, summarize
from signal_audit import SignalAudit
from signals import intraday_signal

SYMBOLS = [
    "BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT",
    "LTC","BCH","UNI","NEAR","ATOM","APT","ARB","OP","SUI","INJ",
]
INTERVAL = "4h"
WINDOWS = [("oos_1",500,1000),("oos_2",1000,1500),("oos_3",1500,2000),
           ("oos_4",2000,2500),("oos_5",2500,3000)]
BODY_THRESHOLDS = (0.30, 0.35, 0.38, 0.40, 0.42, 0.45, 0.50)
POLICIES = ("TP1", "TP3")

def collect_signals(symbol, rows, trend_rows, start, end, cfg, body_min):
    audit = SignalAudit()
    signals = []
    i = max(70, start)
    boundary = min(end, len(rows) - 1)
    while i < boundary:
        window = rows[:i + 1]
        trend_window = [x for x in trend_rows if int(x[0]) <= int(rows[i][0])]
        replay_cfg = {
            **deepcopy(cfg),
            "adaptive_thresholds_enabled": False,
            "target_optimization_enabled": False,
            "signal_confirmation_body_min": float(cfg.get("signal_confirmation_body_min", 0.35)),
            "signal_early_retest_body_min": float(body_min),
            "signal_allow_early_retest": True,
            "signal_require_retest": True,
            "signal_require_sweep": False,
        }
        signal = intraday_signal(
            symbol, interval=INTERVAL, limit=min(180, len(window)),
            min_vol_x=None,
            min_hour_vol=float(cfg.get("buy_fast_min_hour_vol", 50000)),
            chg24=0,
            min_potential_pct=float(cfg.get("signal_min_potential_pct", 5)),
            max_potential_pct=float(cfg.get("signal_max_potential_pct", 300)),
            min_score=None, min_rr=None, cfg=replay_cfg,
            historical_data=window, historical_trend_data=trend_window,
            record_history=False, audit=audit,
        )
        if signal:
            signals.append((i, signal))
        i += 1
    return signals

def main():
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)
    pooled = {f"{t:.2f}": {p: [] for p in POLICIES} for t in BODY_THRESHOLDS}
    windows = []

    for wn, start, end in WINDOWS:
        item = {"window": wn, "start_bar": start, "end_bar": end, "variants": {}}
        for threshold in BODY_THRESHOLDS:
            key = f"{threshold:.2f}"
            collected = {p: [] for p in POLICIES}
            signal_count = 0
            for symbol in SYMBOLS:
                rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
                trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
                if len(rows) < end:
                    continue
                signals = collect_signals(symbol, rows, trend, start, end, cfg, threshold)
                signal_count += len(signals)
                for idx, signal in signals:
                    for policy in POLICIES:
                        result = resolve_exit(rows, idx, signal, policy, end)
                        if result is None:
                            continue
                        result.update({"symbol": symbol, "signal_index": idx})
                        collected[policy].append(result)
                        pooled[key][policy].append(result)

            item["variants"][key] = {"signals": signal_count, "policies": {}}
            for policy in POLICIES:
                m = summarize(collected[policy])
                m["sample_met_20_closed"] = m["closed_decisive"] >= 20
                item["variants"][key]["policies"][policy] = m
        windows.append(item)

    pooled_out = {}
    for threshold in BODY_THRESHOLDS:
        key = f"{threshold:.2f}"
        pooled_out[key] = {}
        for policy in POLICIES:
            m = summarize(pooled[key][policy])
            m["sample_met_20_closed"] = m["closed_decisive"] >= 20
            pooled_out[key][policy] = m

    baseline = pooled_out["0.42"]
    for key in pooled_out:
        for policy in POLICIES:
            p = pooled_out[key][policy]["precision_pct"]
            b = baseline[policy]["precision_pct"]
            pooled_out[key][policy]["delta_vs_042_pp"] = (
                round(float(p) - float(b), 2)
                if p is not None and b is not None else None
            )

    informative = [
        w for w in windows
        if any(
            w["variants"][f"{t:.2f}"]["policies"]["TP3"]["closed_decisive"] > 0
            for t in BODY_THRESHOLDS
        )
    ]
    for key in pooled_out:
        deltas = []
        for w in informative:
            row = w["variants"][key]["policies"]["TP3"]
            base = w["variants"]["0.42"]["policies"]["TP3"]
            if row["precision_pct"] is not None and base["precision_pct"] is not None:
                deltas.append(row["precision_pct"] - base["precision_pct"])
        pooled_out[key]["tp3_nonnegative_window_delta"] = bool(deltas) and all(x >= 0 for x in deltas)
        pooled_out[key]["research_eligible"] = (
            key != "0.42"
            and pooled_out[key]["TP3"]["sample_met_20_closed"]
            and pooled_out[key]["tp3_nonnegative_window_delta"]
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "universe": SYMBOLS,
        "method": "current 4h signal stream with early retest enabled; vary only early-retest body threshold",
        "windows": windows,
        "body_thresholds": list(BODY_THRESHOLDS),
        "exit_policies": list(POLICIES),
        "pooled": pooled_out,
        "production_default": float(cfg.get("signal_early_retest_body_min", 0.42)),
        "promotion_rule": "A non-default threshold must have >=20 decisive TP3 outcomes, non-negative TP3 precision delta versus 0.42 in every informative window, and fresh forward/shadow validation. No result is a production change.",
        "warning": "Research-only. No BUY, execution, SPOT, leverage, or withdrawal policy is changed.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_early_retest_policy.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
