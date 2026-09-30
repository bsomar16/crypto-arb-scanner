#!/usr/bin/env python3
"""OOS sensitivity analysis for BUY rejection thresholds.

This module is research-only. It does not change live signal defaults.
"""
from __future__ import annotations

from copy import deepcopy
from statistics import mean

from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit
from signals import STRATEGY_PROFILES


ENTRY_POLICIES = {
    "current": {"require_retest": True, "require_sweep": False, "allow_early_retest": False},
    "no_early_retest": {"require_retest": True, "require_sweep": False, "allow_early_retest": False},
    "sweep_required": {"require_retest": True, "require_sweep": True, "allow_early_retest": True},
    "retest_optional": {"require_retest": False, "require_sweep": False, "allow_early_retest": True},
}


def _precision(rows):
    closed = sum(int(r.get("closed", 0)) for r in rows)
    wins = sum(int(r.get("wins", 0)) for r in rows)
    return wins / closed * 100.0 if closed else None


def _run_variant(symbols, intervals, cfg, liquidity_floor, volume_factor, score_floor, rr_floor, entry_policy):
    results = []
    base = dict(cfg)
    for symbol in symbols:
        for interval in intervals:
            rows = fetch_history(symbol, interval, int(base.get("backtest_bars", 3000)))
            trend_interval = "4h" if interval in ("5m", "15m", "1h") else "1d"
            trend_rows = fetch_history(symbol, trend_interval, max(500, int(base.get("backtest_bars", 3000)) // 8))
            if len(rows) < 200:
                continue
            n = len(rows)
            train = min(int(base.get("validation_train_bars", 1500)), n)
            val = min(int(base.get("validation_validation_bars", 750)), max(0, n - train))
            start, end = train + val, n
            if end - start < 50:
                continue
            audit = SignalAudit()
            variant_cfg = deepcopy(base)
            variant_cfg["sensitivity_mode"] = True
            profile_floor = next(p["min_vol_x"] for p in STRATEGY_PROFILES.values() if p["interval"] == interval)
            volume_floor = profile_floor * float(volume_factor)
            trades = replay_symbol(
                symbol, interval, rows, trend_rows, start, end, variant_cfg,
                audit=audit, min_hour_vol=liquidity_floor,
                min_vol_x=volume_floor, min_score=score_floor, min_rr=rr_floor,
                entry_policy=entry_policy,
            )
            summary = summarize(trades)
            shadow = audit.near_miss_shadow_snapshot()
            results.append({
                "symbol": symbol, "interval": interval, "summary": summary,
                "audit": audit.snapshot(), "near_miss_shadow_count": len(shadow),
            })
    return results


def compare_variant(label, results):
    closed = sum(r["summary"]["closed"] for r in results)
    wins = sum(r["summary"]["wins"] for r in results)
    detected = sum(r["summary"]["signals"] for r in results)
    rejected = sum(sum(v for k, v in r["audit"].items() if k != "qualified") for r in results)
    return {
        "variant": label,
        "detected_signals": detected,
        "closed": closed,
        "wins": wins,
        "losses": max(0, closed - wins),
        "precision_pct": round(wins / closed * 100.0, 2) if closed else None,
        "rejected": rejected,
    }


def run_sensitivity(cfg=None, symbols=None, intervals=None, variants=None):
    """Run controlled OOS variants and return a comparison report.

    The baseline is always the current production thresholds. Variants are
    evaluated on the same symbols, intervals, and OOS boundaries.
    """
    base = dict(cfg or {})
    symbols = [str(x).upper() for x in (symbols or base.get("backtest_symbols") or
        ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"])]
    intervals = [x for x in (intervals or base.get("backtest_intervals") or
        [p["interval"] for p in STRATEGY_PROFILES.values()])
        if x in {p["interval"] for p in STRATEGY_PROFILES.values()}]
    intervals = [x for x in intervals if x in {p["interval"] for p in STRATEGY_PROFILES.values()}]

    current_liquidity = float(base.get("buy_fast_min_hour_vol", 75000))
    current_score = {p["interval"]: float(p["min_score"]) for p in STRATEGY_PROFILES.values()}
    current_rr = {p["interval"]: float(p["min_rr"]) for p in STRATEGY_PROFILES.values()}
    variants = variants or [
        {"name": "baseline", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "liquidity_-20pct", "liquidity_floor": current_liquidity * 0.80, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "liquidity_-40pct", "liquidity_floor": current_liquidity * 0.60, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "volume_-5pct", "liquidity_floor": current_liquidity, "volume_factor": 0.95, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "no_early_retest", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "no_early_retest"},
        {"name": "sweep_required", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": 0.0, "entry_policy": "sweep_required"},
        {"name": "score_-2", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": -2.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "score_-4", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": -4.0, "rr_delta": 0.0, "entry_policy": "current"},
        {"name": "rr_-0.10", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": -0.10, "entry_policy": "current"},
        {"name": "rr_-0.20", "liquidity_floor": current_liquidity, "volume_factor": 1.0, "score_delta": 0.0, "rr_delta": -0.20, "entry_policy": "current"},
    ]
    reports = []
    for variant in variants:
        volume_factor = variant.get("volume_factor", 1.0)
        score_delta = float(variant.get("score_delta", 0.0))
        rr_delta = float(variant.get("rr_delta", 0.0))
        policy = ENTRY_POLICIES[str(variant.get("entry_policy", "current"))]
        rows = _run_variant(symbols, intervals, base, float(variant["liquidity_floor"]), float(volume_factor), {k: max(0.0, v + score_delta) for k, v in current_score.items()}, {k: max(0.0, v + rr_delta) for k, v in current_rr.items()}, policy)
        reports.append(compare_variant(variant["name"], rows))
    baseline = reports[0] if reports else {}
    for report in reports:
        report["delta_signals_vs_baseline"] = report["detected_signals"] - baseline.get("detected_signals", report["detected_signals"])
        report["delta_precision_pp_vs_baseline"] = (
            round(report["precision_pct"] - baseline["precision_pct"], 2)
            if report["precision_pct"] is not None and baseline.get("precision_pct") is not None else None
        )
    return {
        "method": "same OOS windows, controlled threshold/policy variants",
        "historical_only": True,
        "target_precision_pct": float(base.get("validation_target_precision_pct", 80)),
        "variants": reports,
    }


def format_report(report):
    lines = ["OOS BUY THRESHOLD SENSITIVITY", "Historical evidence only; not a future-performance guarantee."]
    for r in report.get("variants", []):
        p = "-" if r["precision_pct"] is None else f'{r["precision_pct"]:.1f}%'
        d = r.get("delta_precision_pp_vs_baseline")
        ds = r.get("delta_signals_vs_baseline", 0)
        lines.append(f'{r["variant"]}: signals={r["detected_signals"]} closed={r["closed"]} W/L={r["wins"]}/{r["losses"]} precision={p} Δsignals={ds} Δprecision_pp={d}')
    return "\n".join(lines)
