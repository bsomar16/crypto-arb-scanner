#!/usr/bin/env python3
"""Bounded, outcome-aware threshold adaptation for crypto SPOT signals.

Live outcomes may tighten or slightly relax screening thresholds, but only
inside explicit safety bounds. A small sample never changes the thresholds.
"""

MIN_SAMPLE = 20


def adaptive_thresholds(interval, setup_type, base_score, base_vol_x, base_rr,
                        stats=None, cfg=None):
    cfg = cfg or {}
    stats = stats or {}
    sample = int(stats.get("sample", 0) or 0)
    if sample < int(cfg.get("adaptive_min_samples", MIN_SAMPLE)):
        return {
            "min_score": float(base_score),
            "min_vol_x": float(base_vol_x),
            "min_rr": float(base_rr),
            "mode": "BASE",
            "sample": sample,
        }

    win = stats.get("win_pct")
    rates = stats.get("milestone_rates") or {}
    mfe = float(stats.get("avg_mfe_pct", 0.0) or 0.0)
    mae = float(stats.get("avg_mae_pct", 0.0) or 0.0)
    if win is None:
        return {
            "min_score": float(base_score), "min_vol_x": float(base_vol_x),
            "min_rr": float(base_rr), "mode": "BASE", "sample": sample,
        }

    early = (
        float(rates.get("5", 0.0)) * 0.50
        + float(rates.get("10", 0.0)) * 0.30
        + float(rates.get("20", 0.0)) * 0.20
    )
    quality = (
        float(win) * 0.45
        + max(0.0, min(100.0, early)) * 0.30
        + max(0.0, min(100.0, mfe * 2.0)) * 0.15
        + (100.0 - max(0.0, min(100.0, -mae * 8.0))) * 0.10
    )

    # Adaptation is deliberately narrow: good evidence can relax screening
    # slightly; weak evidence tightens it. Hard floors prevent overfitting.
    score_delta = max(-4.0, min(4.0, (quality - 60.0) / 10.0))
    vol_delta = max(-0.10, min(0.10, (quality - 60.0) / 80.0))
    rr_delta = max(-0.10, min(0.10, (quality - 60.0) / 80.0))

    floor_score = float(cfg.get("adaptive_min_score_floor", base_score))
    floor_vol = float(cfg.get("adaptive_min_vol_x_floor", base_vol_x))
    floor_rr = float(cfg.get("adaptive_min_rr_floor", base_rr))

    min_score = max(floor_score, float(base_score) - score_delta)
    min_vol_x = max(floor_vol, float(base_vol_x) - vol_delta)
    min_rr = max(floor_rr, float(base_rr) - rr_delta)

    return {
        "min_score": round(min_score, 3),
        "min_vol_x": round(min_vol_x, 3),
        "min_rr": round(min_rr, 3),
        "mode": "ADAPTIVE",
        "sample": sample,
        "quality": round(quality, 2),
        "early_milestone_pct": round(early, 2),
        "scope": stats.get("scope", ""),
    }
