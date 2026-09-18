#!/usr/bin/env python3
"""Confirmed SPOT entry engine: liquidity sweep -> reclaim -> BOS -> retest -> confirmation."""
from __future__ import annotations

def _pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0

def _body_strength(o, h, l, c):
    return abs(c - o) / max(h - l, 1e-12)

def evaluate_entry(closes, highs, lows, opens, interval, atr=None, require_retest=True, require_sweep=True):
    """Return a causal long-entry confirmation or None using only closed candles."""
    n = len(closes)
    if n < 45 or len(opens) != n or len(highs) != n or len(lows) != n:
        return None
    atr = float(atr or 0) or max(closes[-1] * 0.01, 1e-12)
    price = closes[-1]
    tol = max(atr * 0.18, price * 0.0015)
    sweep = None
    for i in range(n - 3, max(20, n - 10), -1):
        prior_low = min(lows[max(0, i - 18):i])
        if lows[i] < prior_low - tol * 0.15 and closes[i] > prior_low:
            sweep = {"index": i, "level": prior_low, "low": lows[i], "reclaim": closes[i]}
            break

    bos = None
    if sweep:
        pre_high = max(highs[max(0, sweep["index"] - 18):sweep["index"]])
        for i in range(sweep["index"] + 1, min(n - 1, sweep["index"] + 9) + 1):
            if closes[i] > pre_high + tol * 0.10:
                bos = {"index": i, "level": pre_high, "close": closes[i]}
                break
    if bos is None:
        lookback_high = max(highs[-18:-3])
        for i in range(n - 3, n):
            if closes[i] > lookback_high + tol * 0.10:
                bos = {"index": i, "level": lookback_high, "close": closes[i]}
                break
    if bos is None or (require_sweep and sweep is None):
        return None

    broken_level = bos["level"]
    retest = None
    confirm = None
    for i in range(bos["index"] + 1, n):
        if lows[i] <= broken_level + tol and closes[i] >= broken_level - tol * 0.10:
            retest = {"index": i, "level": broken_level, "low": lows[i], "close": closes[i]}
            if i + 1 < n:
                o, h, l, c = opens[i + 1], highs[i + 1], lows[i + 1], closes[i + 1]
                body = _body_strength(o, h, l, c)
                if c > o and c >= broken_level and body >= 0.35:
                    confirm = {"index": i + 1, "open": o, "high": h, "low": l, "close": c, "body_strength": body}
            break

    if require_retest and (retest is None or confirm is None):
        return None
    if confirm is None:
        confirm = {"index": retest["index"], "open": opens[retest["index"]], "high": highs[retest["index"]],
                   "low": lows[retest["index"]], "close": closes[retest["index"]],
                   "body_strength": _body_strength(opens[retest["index"]], highs[retest["index"]],
                                                    lows[retest["index"]], closes[retest["index"]])}

    entry_price = confirm["close"]
    if entry_price <= broken_level:
        return None
    sweep_ok = sweep is not None and sweep["index"] < bos["index"]
    retest_pct = abs(_pct(retest["close"], broken_level)) if retest else 0.0
    extension_pct = max(0.0, _pct(entry_price, broken_level))
    quality = 45.0 + (15 if sweep_ok else 0) + 15 + (12 if retest else 0) + 8
    if confirm["body_strength"] >= 0.55:
        quality += 3
    if extension_pct > 2.0:
        quality -= min(12.0, extension_pct * 2.0)
    quality = max(0.0, min(100.0, quality))
    return {
        "entry_price": entry_price,
        "entry_quality": round(quality, 1),
        "entry_trigger": "SWEEP_RECLAIM_BOS_RETEST_CONFIRM" if sweep_ok else "BOS_RETEST_CONFIRM",
        "liquidity_sweep_confirmed": sweep_ok,
        "reclaim_confirmed": sweep_ok,
        "bos_confirmed": True,
        "retest_confirmed": retest is not None,
        "confirmation_candle": confirm is not None,
        "bos_level": broken_level,
        "sweep_level": sweep["level"] if sweep else None,
        "retest_level": retest["level"] if retest else broken_level,
        "retest_distance_pct": round(retest_pct, 3),
        "extension_pct": round(extension_pct, 3),
        "confirmation_body": round(confirm["body_strength"], 3),
        "trigger_index": confirm["index"],
    }
