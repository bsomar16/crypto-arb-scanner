#!/usr/bin/env python3
"""Research-only 4h exit-policy OOS study.

Keeps the production signal engine frozen and compares three predeclared
exit policies: TP1, TP2 and TP3. No production configuration is changed.
The signal stream is generated once per symbol/window, then each policy
resolves the same signal events independently so the study measures exits,
not a tuned signal-selection rule.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean

from live_replay import INTERVAL_MINUTES, _bars_for_hold, fetch_history
from signal_audit import SignalAudit
from signals import STRATEGY_PROFILES, intraday_signal

SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT",
    "LTC", "BCH", "UNI", "NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ",
]
INTERVAL = "4h"
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]
EXIT_POLICIES = ("TP1", "TP2", "TP3")
HOLD_MAX_HOURS = int(next(p for p in STRATEGY_PROFILES.values() if p["interval"] == INTERVAL).get("hold_max_hours", 120))


def resolve_exit(rows, signal_index, signal, target_key, max_index):
    """Resolve one frozen signal against one frozen target policy."""
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    target = float(signal["t" + target_key[2:].lower()])
    horizon = _bars_for_hold(INTERVAL, HOLD_MAX_HOURS)
    boundary = min(len(rows), int(max_index))
    end = min(boundary, signal_index + 1 + horizon)
    mfe = 0.0
    mae = 0.0

    for j in range(signal_index + 1, end):
        high = float(rows[j][2])
        low = float(rows[j][3])
        mfe = max(mfe, (high / entry - 1.0) * 100.0)
        mae = min(mae, (low / entry - 1.0) * 100.0)

        # Conservative ordering: stop wins if both stop and target are touched.
        if low <= stop:
            return {
                "outcome": "LOSS",
                "exit_price": stop,
                "exit_i": j,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "hold_bars": j - signal_index,
                "ret_pct": (stop / entry - 1.0) * 100.0,
                "censored": False,
            }
        if high >= target:
            return {
                "outcome": "WIN",
                "exit_price": target,
                "exit_i": j,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "hold_bars": j - signal_index,
                "ret_pct": (target / entry - 1.0) * 100.0,
                "censored": False,
            }

    if end <= signal_index:
        return None
    close_price = float(rows[end - 1][4])
    complete = end >= min(len(rows), signal_index + 1 + horizon)
    return {
        "outcome": "EXPIRED",
        "exit_price": close_price,
        "exit_i": end - 1,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "hold_bars": end - 1 - signal_index,
        "ret_pct": (close_price / entry - 1.0) * 100.0,
        "censored": not complete,
    }


def summarize(rows):
    decisive = [r for r in rows if r["outcome"] in {"WIN", "LOSS"} and not r["censored"]]
    wins = sum(r["outcome"] == "WIN" for r in decisive)
    losses = len(decisive) - wins
    complete = [r for r in rows if not r["censored"]]
    returns = [float(r["ret_pct"]) for r in decisive]
    return {
        "signals": len(rows),
        "closed_decisive": len(decisive),
        "wins": wins,
        "losses": losses,
        "expired": sum(r["outcome"] == "EXPIRED" for r in complete),
        "censored": sum(bool(r["censored"]) for r in rows),
        "precision_pct": round(100.0 * wins / len(decisive), 2) if decisive else None,
        "avg_return_pct": round(mean(returns), 3) if returns else None,
        "avg_mfe_pct": round(mean(float(r["mfe_pct"]) for r in decisive), 3) if decisive else None,
        "avg_mae_pct": round(mean(float(r["mae_pct"]) for r in decisive), 3) if decisive else None,
        "avg_hold_hours": round(mean(float(r["hold_bars"]) * INTERVAL_MINUTES[INTERVAL] / 60.0 for r in decisive), 2) if decisive else None,
    }


def collect_signals(symbol, rows, trend_rows, start, end, cfg):
    """Generate the frozen production signal stream without exit-policy effects."""
    audit = SignalAudit()
    signals = []
    i = max(70, start)
    boundary = min(end, len(rows) - 1)

    while i < boundary:
        window = rows[: i + 1]
        trend_window = [x for x in trend_rows if int(x[0]) <= int(rows[i][0])]
        replay_cfg = {**deepcopy(cfg), "adaptive_thresholds_enabled": False, "target_optimization_enabled": False}
        signal = intraday_signal(
            symbol,
            interval=INTERVAL,
            limit=min(180, len(window)),
            min_vol_x=None,
            min_hour_vol=float(cfg.get("buy_fast_min_hour_vol", 75000)),
            chg24=0,
            min_potential_pct=float(cfg.get("signal_min_potential_pct", 5)),
            max_potential_pct=float(cfg.get("signal_max_potential_pct", 300)),
            min_score=None,
            min_rr=None,
            cfg=replay_cfg,
            historical_data=window,
            historical_trend_data=trend_window,
            record_history=False,
            audit=audit,
        )
        if signal:
            signals.append((i, signal))
        i += 1
    return signals


def main():
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)

    windows = []
    pooled = {policy: [] for policy in EXIT_POLICIES}

    for window_name, start, end in WINDOWS:
        item = {"window": window_name, "start_bar": start, "end_bar": end, "signals": 0, "policies": {}}
        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue

            signals = collect_signals(symbol, rows, trend, start, end, cfg)
            item["signals"] += len(signals)
            for policy in EXIT_POLICIES:
                results = []
                for signal_index, signal in signals:
                    result = resolve_exit(rows, signal_index, signal, policy, end)
                    if result is None:
                        continue
                    result.update({
                        "symbol": symbol,
                        "signal_index": signal_index,
                        "candle_open_time": int(signal.get("candle_open_time", rows[signal_index][0])),
                    })
                    results.append(result)
                    pooled[policy].append(result)

                item["policies"].setdefault(policy, []).extend(results)

        # Replace raw rows with compact metrics for the report.
        compact = {"window": window_name, "start_bar": start, "end_bar": end, "signals": item["signals"], "policies": {}}
        for policy in EXIT_POLICIES:
            metrics = summarize(item["policies"].get(policy, []))
            metrics["sample_met_20_closed"] = metrics["closed_decisive"] >= 20
            compact["policies"][policy] = metrics
        windows.append(compact)

    pooled_out = {}
    for policy in EXIT_POLICIES:
        metrics = summarize(pooled[policy])
        metrics["sample_met_20_closed"] = metrics["closed_decisive"] >= 20
        pooled_out[policy] = metrics

    baseline = pooled_out["TP3"]
    for policy in EXIT_POLICIES:
        precision = pooled_out[policy]["precision_pct"]
        pooled_out[policy]["delta_vs_tp3_pp"] = (
            round(float(precision) - float(baseline["precision_pct"]), 2)
            if precision is not None and baseline["precision_pct"] is not None else None
        )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "research_only": True,
        "universe": SYMBOLS,
        "method": "current production 4h signal stream with frozen TP1/TP2/TP3 exit resolution",
        "exit_policies": list(EXIT_POLICIES),
        "policy_note": "Signal generation is held fixed; exit policies are evaluated independently on the same signal events. No policy is promoted from this study.",
        "windows": windows,
        "pooled": pooled_out,
        "promotion_rule": "Exit policy must improve/preserve precision across informative windows, have >=20 decisive pooled outcomes, and pass fresh forward/shadow validation before any production consideration.",
        "warning": "Research-only. No production BUY, target, stop, or execution rule is changed.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with open("state/oos_4h_exit_policy.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
