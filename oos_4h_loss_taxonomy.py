#!/usr/bin/env python3
"""Research-only 4h loss taxonomy and trade-level diagnostics.

No thresholds are selected here. Entry-time observables and path-dependent
diagnostics are deliberately separated so outcome-after-entry information
cannot leak into a production filter.
"""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean
from live_replay import fetch_history, replay_symbol

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]
INTERVAL = "4h"
WINDOWS = [("oos_1", 750, 1500), ("oos_2", 1500, 2250), ("oos_3", 2250, 3000)]


def _f(row, key, default=0.0):
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def classify_loss(row):
    """PATH-DEPENDENT diagnosis only. Never use these labels as filters."""
    mfe = _f(row, "mfe_pct")
    mae = _f(row, "mae_pct")
    potential = max(_f(row, "potential_pct"), 0.0)
    rr = _f(row, "rr")
    hold = _f(row, "estimated_hold_hours")
    if mfe < 1.0 and mae <= -1.0:
        return "immediate_failed_followthrough"
    if potential > 0 and mfe < 0.25 * potential and mae <= -1.5:
        return "low_followthrough_vs_target"
    if mfe >= 8.0 and mae <= -2.0:
        return "high_mfe_but_stop_failure"
    if rr > 0 and mfe >= max(2.0, 0.5 * potential) and mae <= -1.5:
        return "late_reversal_after_progress"
    if hold <= 8 and mae <= -1.5:
        return "fast_stopout"
    return "other_loss"


def _entry_observables(row):
    """ENTRY-TIME fields only; safe candidates for future hypotheses."""
    ob = row.get("order_block") or {}
    vol = row.get("volatility") or {}
    zero = row.get("zero_inverse") or {}
    return {
        "setup": str(row.get("setup_type") or row.get("setup") or "UNKNOWN"),
        "trend_4h": str(row.get("trend_4h") or "UNKNOWN"),
        "expansion_state": str(row.get("expansion_state") or "UNKNOWN"),
        "structure_score": round(_f(row, "structure_score"), 2),
        "entry_extension_pct": round(_f(row, "entry_extension_pct", row.get("expansion_extension_pct", 0)), 3),
        "vol_x": round(_f(row, "vol_x", row.get("expansion_volume_ratio", 0)), 3),
        "order_block_bullish": bool(ob.get("bullish", row.get("order_block_bullish", False))),
        "zero_inverse_bullish": bool(
            zero.get("bullish_reversal") or zero.get("bullish_reclaim")
            or row.get("zero_inverse_bullish", False)
        ),
        "volatility_state": str(vol.get("state") or row.get("volatility_state") or "UNKNOWN"),
    }


def _cost_model(cfg):
    fees = cfg.get("spot_taker_fee_pct") or {}
    fee_pct = float(fees.get("binance", fees.get("BINANCE", 0.0)) or 0.0)
    slippage_pct = float(cfg.get("realtime_slippage_reserve_pct", 0.0) or 0.0)
    return {
        "fee_pct_per_side": fee_pct,
        "slippage_reserve_pct_per_side": slippage_pct,
        "round_trip_cost_pct": 2.0 * (fee_pct + slippage_pct),
        "note": "Scenario-adjusted using configured Binance taker fee and slippage reserve; not realized execution cost.",
    }


def _net_r(row, costs):
    entry = _f(row, "entry")
    stop = _f(row, "stop")
    target = _f(row, "t3", row.get("target", entry))
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None
    risk_pct = (entry - stop) / entry * 100.0
    if risk_pct <= 0:
        return None
    cost = costs["round_trip_cost_pct"]
    if row.get("outcome") == "WIN":
        return ((target - entry) / entry * 100.0 - cost) / risk_pct
    if row.get("outcome") == "LOSS":
        return (-risk_pct - cost) / risk_pct
    return None


def _max_losing_streak(rs):
    best = cur = 0
    for value in rs:
        if value < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_drawdown(rs):
    equity = peak = drawdown = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _trade_metrics(rows, cfg):
    closed = [r for r in rows if r.get("outcome") in ("WIN", "LOSS")]
    costs = _cost_model(cfg)
    rs = [x for x in (_net_r(r, costs) for r in closed) if x is not None]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    win_rows = [r for r in closed if r.get("outcome") == "WIN"]

    def concentration(key_fn):
        counts = Counter(key_fn(r) for r in win_rows)
        return round(max(counts.values()) / len(win_rows) * 100.0, 2) if win_rows else None

    months = []
    for r in closed:
        ts = r.get("entry_time")
        if ts:
            try:
                months.append(datetime.fromtimestamp(float(ts) / 1000.0, timezone.utc).strftime("%Y-%m"))
            except (TypeError, ValueError, OverflowError):
                pass

    gross_expectancy = mean(rs) if rs else None
    return {
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "precision_pct": round(len(wins) / len(closed) * 100.0, 2) if closed else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and wins else None,
        "average_win_r": round(mean(wins), 3) if wins else None,
        "average_loss_r": round(mean(losses), 3) if losses else None,
        "expectancy_r_after_configured_cost_scenario": round(gross_expectancy, 3) if gross_expectancy is not None else None,
        "max_losing_streak": _max_losing_streak(rs),
        "max_drawdown_r": round(_max_drawdown(rs), 3),
        "average_hold_hours": round(mean(_f(r, "estimated_hold_hours") for r in closed), 2) if closed else None,
        "average_mfe_pct": round(mean(_f(r, "mfe_pct") for r in closed), 3) if closed else None,
        "average_mae_pct": round(mean(_f(r, "mae_pct") for r in closed), 3) if closed else None,
        "trade_frequency_closed_per_100_bars": round(len(closed) / 750.0 * 100.0, 3),
        "coverage_pct": round(len(closed) / len(rows) * 100.0, 2) if rows else None,
        "win_concentration_coin_pct": concentration(lambda r: str(r.get("coin") or "UNKNOWN")),
        "win_concentration_month_pct": round(max(Counter(months).values()) / len(months) * 100.0, 2) if months else None,
        "cost_model": costs,
    }


def _taxonomy(rows):
    losses = [r for r in rows if r.get("outcome") == "LOSS"]
    total_loss_r = sum(abs(_net_r(r, _cost_model(_CFG)) or 0.0) for r in losses)
    by_mode = {}
    for r in losses:
        mode = classify_loss(r)
        item = by_mode.setdefault(mode, {"count": 0, "loss_r": 0.0})
        item["count"] += 1
        item["loss_r"] += abs(_net_r(r, _cost_model(_CFG)) or 0.0)
    for item in by_mode.values():
        item["share_total_loss_r_pct"] = round(item["loss_r"] / total_loss_r * 100.0, 2) if total_loss_r else None
        item["loss_r"] = round(item["loss_r"], 3)
    ranked = sorted(by_mode.items(), key=lambda kv: (-float(kv[1]["share_total_loss_r_pct"] or 0), -kv[1]["count"], kv[0]))

    entry = {}
    for r in losses:
        for key, value in _entry_observables(r).items():
            bucket = entry.setdefault(key, {})
            label = str(value)
            bucket[label] = bucket.get(label, 0) + 1

    # At most one 2-D cross-tab: setup x expansion state.
    cross = {}
    for r in losses:
        e = _entry_observables(r)
        key = f'{e["setup"]} × {e["expansion_state"]}'
        cross[key] = cross.get(key, 0) + 1

    return {
        "losses": len(losses),
        "total_loss_r_abs": round(total_loss_r, 3),
        "failure_modes_ranked_by_share_of_total_loss_r": [
            {"mode": mode, **item} for mode, item in ranked
        ],
        "entry_time_observables": entry,
        "entry_time_cross_tab_setup_x_expansion": cross,
        "path_dependent_labels": sorted({classify_loss(r) for r in losses}),
        "guardrail": "Entry-time fields may become hypotheses; path-dependent labels are diagnosis only and can never be filters.",
    }


def main():
    global _CFG
    with open("config.json", encoding="utf-8") as f:
        _CFG = json.load(f)

    windows = []
    pooled = []
    for name, start, end in WINDOWS:
        trades = []
        for symbol in SYMBOLS:
            rows = fetch_history(symbol, INTERVAL, int(_CFG.get("backtest_bars", 3000)))
            trend = fetch_history(symbol, "1d", max(500, int(_CFG.get("backtest_bars", 3000)) // 8))
            if len(rows) < end:
                continue
            batch = replay_symbol(
                symbol, INTERVAL, rows, trend, start, end,
                cfg=deepcopy(_CFG),
                entry_policy={"require_retest": True, "require_sweep": False, "allow_early_retest": False},
            )
            for trade in batch:
                idx = int(trade.get("signal_index", 0))
                if 0 <= idx < len(rows):
                    trade["entry_time"] = int(rows[idx][0])
            trades.extend(batch)

        pooled.extend(trades)
        windows.append({
            "window": name,
            "start_bar": start,
            "end_bar": end,
            "signals": len(trades),
            "closed": sum(r.get("outcome") in ("WIN", "LOSS") for r in trades),
            "baseline_trade_metrics": _trade_metrics(trades, _CFG),
            "loss_taxonomy": _taxonomy(trades),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only": True,
        "candidate": "baseline",
        "method": "Current production 4h replay across three rolling 750-bar OOS windows.",
        "windows": windows,
        "pooled": {
            "baseline_trade_metrics": _trade_metrics(pooled, _CFG),
            "loss_taxonomy": _taxonomy(pooled),
        },
        "interpretation_guardrails": [
            "Descriptive only; no threshold selection.",
            "The three PR #99 windows are contaminated for the structure>=50 + BASE hypothesis and must not be tuned against again.",
            "Path-dependent labels are diagnosis only and never filters.",
            "Cost-adjusted R is scenario-adjusted from configured fee/slippage reserves, not realized execution P&L.",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    with open("state/oos_4h_loss_taxonomy.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
