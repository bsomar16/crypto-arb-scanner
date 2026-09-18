#!/usr/bin/env python3
"""Causal early-expansion classifier for crypto SPOT BUY setups."""

def _pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0

def classify_expansion(closes, highs, lows, volumes, atr, entry_index=None):
    """Score whether a confirmed entry is early in a new expansion leg.

    Uses only candles up to the supplied entry index. It deliberately does not
    use future candles, so the result is suitable for live signals and backtests.
    """
    n = len(closes)
    i = n - 1 if entry_index is None else min(max(int(entry_index), 20), n - 1)
    if i < 20:
        return {"state": "UNKNOWN", "score": 0.0, "extension_pct": 0.0,
                "compression": 0.0, "expansion": 0.0, "late": False}

    price = float(closes[i])
    lookback = closes[max(0, i - 20):i]
    prior_high = max(highs[max(0, i - 20):i])
    prior_low = min(lows[max(0, i - 20):i])
    move_from_low = _pct(price, prior_low)
    move_from_high = _pct(price, prior_high)

    recent_ranges = [(float(highs[j]) - float(lows[j])) / float(closes[j])
                     for j in range(max(1, i - 5), i + 1) if closes[j]]
    base_ranges = [(float(highs[j]) - float(lows[j])) / float(closes[j])
                   for j in range(max(1, i - 20), max(1, i - 5)) if closes[j]]
    recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0.0
    base_range = sum(base_ranges) / len(base_ranges) if base_ranges else 0.0
    expansion = recent_range / base_range if base_range else 1.0

    recent_vol = sum(float(v) for v in volumes[max(0, i - 2):i + 1]) / 3.0
    base_vols = volumes[max(0, i - 22):max(0, i - 2)]
    base_vol = sum(float(v) for v in base_vols) / len(base_vols) if base_vols else 0.0
    vol_ratio = recent_vol / base_vol if base_vol else 1.0

    atr_pct = float(atr) / price * 100.0 if price and atr else 0.0
    # Extension is measured from the prior 20-candle low. Very large moves
    # can still qualify when a fresh compression/re-expansion is forming.
    extension_pct = max(0.0, move_from_low)
    near_breakout = price >= prior_high * 0.995 if prior_high else False

    score = 0.0
    if 0.0 <= move_from_high <= 2.0:
        score += 18
    elif move_from_high > 2.0:
        score += 8
    if 1.15 <= expansion <= 2.5:
        score += 22
    elif expansion > 2.5:
        score += 10
    if vol_ratio >= 1.5:
        score += 20
    elif vol_ratio >= 1.2:
        score += 12
    if near_breakout:
        score += 15
    if 0.5 <= atr_pct <= 8:
        score += 10

    # Penalize chasing an already extended leg, but do not automatically reject
    # it: continuation can become a new leg after a reclaim/retest.
    if extension_pct > 20:
        score -= min(25, (extension_pct - 20) * 0.45)
    if extension_pct > 50:
        score -= min(25, (extension_pct - 50) * 0.30)

    if score >= 70:
        state = "EARLY_EXPANSION"
    elif score >= 50:
        state = "EXPANSION"
    elif extension_pct > 50:
        state = "LATE_EXTENSION"
    else:
        state = "BASE"

    return {
        "state": state,
        "score": round(max(0.0, min(100.0, score)), 1),
        "extension_pct": round(extension_pct, 2),
        "compression": round(max(0.0, 1.0 - min(1.0, expansion)), 3),
        "expansion": round(expansion, 3),
        "volume_ratio": round(vol_ratio, 2),
        "near_breakout": near_breakout,
        "atr_pct": round(atr_pct, 2),
    }
