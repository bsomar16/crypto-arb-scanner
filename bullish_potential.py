#!/usr/bin/env python3
"""Multi-timeframe bullish continuation quality classification.

This is deliberately a classification layer, not a win-rate or price forecast.
It combines higher-timeframe trend, structure, expansion, volatility and
confirmation evidence to distinguish ordinary BUYs from stronger continuation
setups that may justify wider target ladders.
"""

from __future__ import annotations


def classify_bullish_potential(
    interval,
    trend,
    structure,
    expansion,
    context,
    entry,
    score,
    potential_pct,
):
    """Return a deterministic bullish-continuation quality assessment."""
    points = 0.0
    drivers = []
    flags = []

    trend_state = (trend or {}).get("state", "MIXED")
    if trend_state == "BULLISH":
        points += 25
        drivers.append(f"{(trend or {}).get('interval', 'higher')} bullish")
        flags.append("higher_timeframe_bullish")
    elif trend_state == "MIXED":
        points += 8
    else:
        points -= 20

    if (structure or {}).get("bos"):
        points += 16
        drivers.append("BOS")
        flags.append("bos")
    if (structure or {}).get("higher_lows"):
        points += 10
        drivers.append("higher lows")
        flags.append("higher_lows")
    if (structure or {}).get("compression", 0) >= 0.15:
        points += 8
        drivers.append("compression before expansion")
        flags.append("compression")

    expansion_state = (expansion or {}).get("state", "BASE")
    if expansion_state == "EARLY_EXPANSION":
        points += 18
        drivers.append("early expansion")
        flags.append("early_expansion")
    elif expansion_state == "EXPANSION":
        points += 13
        drivers.append("expansion confirmed")
        flags.append("expansion")
    elif expansion_state == "LATE_EXTENSION":
        points -= 15
    else:
        points += 2

    vol = (context or {}).get("volatility", {})
    if vol.get("state") == "EXPANDING":
        points += 8
        drivers.append("volatility expanding")
        flags.append("volatility_expanding")

    zi = (context or {}).get("zero_inverse", {})
    if zi.get("bullish_reversal"):
        points += 9
        drivers.append("zero-inverse bullish reversal")
        flags.append("zero_inverse_reversal")
    elif zi.get("bullish_reclaim"):
        points += 6
        drivers.append("zero-inverse reclaim")
        flags.append("zero_inverse_reclaim")

    ob = (context or {}).get("order_block", {})
    if ob.get("bullish"):
        points += 8
        drivers.append("bullish order block")
        flags.append("bullish_order_block")
    if ob.get("fresh"):
        points += 3
        drivers.append("fresh order block")
        flags.append("fresh_order_block")

    if (entry or {}).get("bos_confirmed"):
        points += 5
    if (entry or {}).get("retest_confirmed"):
        points += 7
        drivers.append("confirmed retest")
        flags.append("confirmed_retest")
    if (entry or {}).get("confirmation_candle"):
        points += 5
        drivers.append("confirmation candle")
        flags.append("confirmation_candle")

    # Stronger raw model scores add confidence, but never override structural
    # contradictions such as a bearish higher timeframe or late extension.
    if float(score or 0) >= 75:
        points += 8
    elif float(score or 0) >= 65:
        points += 4

    points = max(0.0, min(100.0, points))

    if trend_state == "BEARISH" or expansion_state == "LATE_EXTENSION":
        tier = "STANDARD"
    elif points >= 78 and expansion_state in {"EARLY_EXPANSION", "EXPANSION"}:
        tier = "EXPLOSIVE_CONTINUATION"
    elif points >= 65:
        tier = "STRONG_CONTINUATION"
    elif points >= 52:
        tier = "BULLISH_CONTINUATION"
    else:
        tier = "STANDARD"

    # These are scenario bands, not predictions. They describe the target
    # envelope the current strategy is already willing to model.
    if tier == "EXPLOSIVE_CONTINUATION":
        move_band = "30%+ potential scenario"
    elif tier == "STRONG_CONTINUATION":
        move_band = "15-30% potential scenario"
    elif tier == "BULLISH_CONTINUATION":
        move_band = "8-20% potential scenario"
    else:
        move_band = "strategy target range"

    return {
        "tier": tier,
        "score": round(points, 1),
        "move_band": move_band,
        "drivers": drivers[:10],
        "flags": flags,
        "interval": interval,
        "higher_timeframe": (trend or {}).get("interval"),
        "potential_pct_at_signal": round(float(potential_pct or 0), 1),
    }
