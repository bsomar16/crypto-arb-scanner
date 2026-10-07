#!/usr/bin/env python3
"""Confirmed SPOT entry engine: liquidity sweep -> reclaim -> BOS -> retest -> confirmation."""
from __future__ import annotations

def _pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0

def _body_strength(o, h, l, c):
    return abs(c - o) / max(h - l, 1e-12)

def evaluate_entry(closes, highs, lows, opens, interval, atr=None, require_retest=True, require_sweep=True, allow_early_retest=True, confirmation_body_min=0.35, early_retest_body_min=0.45):
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
            candidate_retest = {"index": i, "level": broken_level, "low": lows[i], "close": closes[i]}
            if retest is None:
                retest = candidate_retest
            if i + 1 < n:
                o, h, l, c = opens[i + 1], highs[i + 1], lows[i + 1], closes[i + 1]
                body = _body_strength(o, h, l, c)
                if c > o and c >= broken_level and body >= float(confirmation_body_min):
                    retest = candidate_retest
                    confirm = {"index": i + 1, "open": o, "high": h, "low": l, "close": c, "body_strength": body, "early": False}
            if confirm is None and allow_early_retest:
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
                body = _body_strength(o, h, l, c)
                if c > o and c > broken_level and body >= float(early_retest_body_min):
                    retest = candidate_retest
                    confirm = {"index": i, "open": o, "high": h, "low": l, "close": c, "body_strength": body, "early": True}
            if confirm is not None:
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
    extension_atr = extension_pct / max((atr / max(broken_level, 1e-12)) * 100.0, 1e-9)
    quality = 42.0 + (14 if sweep_ok else 0) + 16 + (12 if retest else 0)
    quality += min(8.0, max(0.0, confirm["body_strength"]) * 10.0)
    if extension_atr > 0.8:
        quality -= min(18.0, (extension_atr - 0.8) * 8.0)
    elif extension_atr <= 0.35:
        quality += 4
    quality = max(0.0, min(100.0, quality))
    return {
        "entry_price": entry_price,
        "entry_quality": round(quality, 1),
        "entry_trigger": (
            "SWEEP_RECLAIM_BOS_RETEST_EARLY" if sweep_ok and bool(confirm.get("early")) else
            "BOS_RETEST_EARLY" if bool(confirm.get("early")) else
            "SWEEP_RECLAIM_BOS_RETEST_CONFIRM" if sweep_ok else
            "BOS_RETEST_CONFIRM"
        ),
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
        "extension_atr": round(extension_atr, 3),
        "confirmation_body": round(confirm["body_strength"], 3),
        "trigger_index": confirm["index"],
    }


def entry_diagnostics(closes, highs, lows, opens, interval, atr=None, allow_early_retest=True, early_retest_body_min=0.45):
    """Return measurable entry-stage progress for a rejected long setup.

    This is diagnostics only: it never changes the live entry gate. The score is
    derived from observed BOS/retest/confirmation progress and confirmation-body
    distance, not a hard-coded rejection value.
    """
    n = len(closes)
    if n < 45 or len(opens) != n or len(highs) != n or len(lows) != n:
        return {"near_miss_score": 0.0, "bos_confirmed": False, "retest_confirmed": False,
                "confirmation_candle": False, "reason": "insufficient_bars"}
    atr = float(atr or 0) or max(closes[-1] * 0.01, 1e-12)
    price = closes[-1]
    tol = max(atr * 0.18, price * 0.0015)
    sweep = None
    for i in range(n - 3, max(20, n - 10), -1):
        prior_low = min(lows[max(0, i - 18):i])
        if lows[i] < prior_low - tol * 0.15 and closes[i] > prior_low:
            sweep = {"index": i, "level": prior_low}
            break
    bos = None
    if sweep:
        pre_high = max(highs[max(0, sweep["index"] - 18):sweep["index"]])
        for i in range(sweep["index"] + 1, min(n - 1, sweep["index"] + 9) + 1):
            if closes[i] > pre_high + tol * 0.10:
                bos = {"index": i, "level": pre_high}
                break
    if bos is None:
        lookback_high = max(highs[-18:-3])
        for i in range(n - 3, n):
            if closes[i] > lookback_high + tol * 0.10:
                bos = {"index": i, "level": lookback_high}
                break
    if bos is None:
        return {"near_miss_score": 0.0, "bos_confirmed": False, "retest_confirmed": False,
                "confirmation_candle": False, "sweep_confirmed": bool(sweep), "reason": "no_bos"}

    broken_level = bos["level"]
    retests = []
    for i in range(bos["index"] + 1, n):
        if lows[i] <= broken_level + tol and closes[i] >= broken_level - tol * 0.10:
            retests.append({"index": i, "level": broken_level, "close": closes[i]})

    if not retests:
        progress = 35.0
        distance = max(0.0, _pct(price, broken_level))
        return {"near_miss_score": round(progress, 1), "bos_confirmed": True, "retest_confirmed": False,
                "confirmation_candle": False, "sweep_confirmed": bool(sweep),
                "bos_level": broken_level, "price_vs_bos_pct": round(distance, 3),
                "retest_count": 0, "reason": "awaiting_retest"}

    candidates = []
    for retest in retests:
        i = retest["index"]
        if i + 1 < n:
            o, h, l, c = opens[i + 1], highs[i + 1], lows[i + 1], closes[i + 1]
            candidates.append({
                "name": "next_candle", "retest_index": i, "confirmation_index": i + 1,
                "body_strength": _body_strength(o, h, l, c),
                "bullish": c > o and c >= broken_level,
                "threshold": 0.35,
            })
        if allow_early_retest:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            candidates.append({
                "name": "retest_candle", "retest_index": i, "confirmation_index": i,
                "body_strength": _body_strength(o, h, l, c),
                "bullish": c > o and c > broken_level,
                "threshold": float(early_retest_body_min),
            })

    best = max(candidates, key=lambda item: float(item["body_strength"])) if candidates else None
    qualifying = [
        item for item in candidates
        if item["bullish"] and float(item["body_strength"]) >= float(item["threshold"])
    ]
    best_qualifying = max(qualifying, key=lambda item: item["confirmation_index"]) if qualifying else None
    body = float(best["body_strength"]) if best else 0.0
    threshold = float(best["threshold"]) if best else 0.45
    direction_bonus = 1.0 if best and best["bullish"] else 0.0
    body_ratio = min(1.0, body / max(threshold, 1e-9))
    score = 70.0 + 30.0 * body_ratio * direction_bonus
    return {
        "near_miss_score": round(min(100.0, score), 1),
        "bos_confirmed": True,
        "retest_confirmed": True,
        "confirmation_candle": bool(best_qualifying),
        "sweep_confirmed": bool(sweep),
        "bos_level": broken_level,
        "retest_distance_pct": round(abs(_pct(retests[0]["close"], broken_level)), 3),
        "retest_count": len(retests),
        "confirmed_retest_count": len(qualifying),
        "recoverable_later_retest": bool(
            best_qualifying and best_qualifying["retest_index"] > retests[0]["index"]
        ),
        "confirmation_body": round(body, 3),
        "confirmation_body_required": round(threshold, 3),
        "confirmation_body_gap": round(max(0.0, threshold - body), 3),
        "confirmation_direction_bullish": bool(best and best["bullish"]),
        "best_confirmation_index": best_qualifying["confirmation_index"] if best_qualifying else None,
        "reason": "recoverable_later_retest" if best_qualifying and best_qualifying["retest_index"] > retests[0]["index"]
                  else "confirmation_missing" if not best_qualifying else "confirmed",
    }
