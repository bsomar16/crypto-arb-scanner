#!/usr/bin/env python3
"""Causal BUY context: Zero-Inverse momentum, order blocks and volatility."""
from __future__ import annotations
from statistics import mean
import indicators as ind


def _zero_inverse(closes, fast=5, slow=13, signal=5):
    """Project-local zero-line momentum reversal detector."""
    if len(closes) < slow + signal + 2:
        return {"value":0.0,"signal":0.0,"bullish_reclaim":False,"bullish_reversal":False,"state":"NEUTRAL"}
    ef, es = ind.ema(closes, fast), ind.ema(closes, slow)
    raw = [((f-s)/s*100.0) if s else 0.0 for f,s in zip(ef,es)]
    sig = ind.ema(raw, signal)
    value, prev, sv = raw[-1], raw[-2], sig[-1]
    rising = value > prev
    reclaim = prev <= 0.0 < value
    reversal = prev < 0.0 and value > prev and value >= sv and (value-prev) > 0.08
    state = "BULLISH_REVERSAL" if (reclaim or reversal) else "BULLISH" if value > 0 and rising else "BEARISH" if value < 0 and not rising else "NEUTRAL"
    return {"value":round(value,4),"signal":round(sv,4),"bullish_reclaim":bool(reclaim),"bullish_reversal":bool(reversal),"state":state}


def _empty_order_block():
    return {
        "bullish": False,
        "fresh": False,
        "zone_low": None,
        "zone_high": None,
        "midpoint": None,
        "distance_pct": None,
        "strength": 0.0,
        "quality_score": 0.0,
        "status": "NONE",
        "timing": "INVALID",
        "touches": 0,
        "displacement_score": 0.0,
        "volume_score": 0.0,
        "bos_score": 0.0,
        "liquidity_sweep": False,
        "invalidation": None,
        "confluence": "LOCAL",
        "timeframe_context": "local",
    }


def _order_block(closes, highs, lows, vols, atr):
    """Detect and rank the latest bullish order block without making it a hard gate.

    The detector remains causal: only candles available before the current price
    are used. Quality describes the structural zone; timing describes whether
    the current price is still close enough to use that zone for an early entry.
    """
    n = len(closes)
    if n < 30 or atr <= 0 or len(highs) != n or len(lows) != n:
        return _empty_order_block()

    avg_vol = mean(vols[-22:-2]) if len(vols) >= 22 else mean(vols[:-2]) if len(vols) > 2 else 0.0
    best = None
    scan_start = max(5, n - 25)

    for i in range(scan_start, n - 3):
        # Without OHLC opens, a close-to-close bearish candle is our causal
        # bearish-origin proxy. Keep the legacy definition for compatibility.
        if closes[i] >= closes[i - 1]:
            continue

        local_high = max(highs[max(0, i - 5):i + 1])
        displacement_high = max(highs[i + 1:min(n, i + 4)])
        displacement = displacement_high - closes[i]
        if displacement_high <= local_high or displacement < atr:
            continue

        vr = vols[i] / avg_vol if avg_vol > 0 and i < len(vols) else 1.0
        zone_low = lows[i]
        zone_high = max(closes[i], closes[i - 1])
        if zone_high <= zone_low:
            continue

        # BOS strength: how far the displacement moved beyond the prior local high.
        bos_distance = max(0.0, displacement_high - local_high)
        bos_score = min(100.0, bos_distance / atr * 45.0)

        # Displacement quality: 1 ATR is the minimum; 2+ ATR is strong.
        displacement_score = min(100.0, max(0.0, (displacement / atr - 0.75) / 1.75 * 100.0))
        volume_score = min(100.0, max(0.0, (vr - 0.75) / 1.75 * 100.0))

        # A liquidity sweep is inferred when the origin candle briefly trades
        # below the preceding local low before the bullish displacement.
        local_low = min(lows[max(0, i - 5):i])
        liquidity_sweep = lows[i] < local_low if i > 0 else False

        best = {
            "i": i,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "vr": vr,
            "displacement_score": displacement_score,
            "volume_score": volume_score,
            "bos_score": bos_score,
            "liquidity_sweep": liquidity_sweep,
        }

    if not best:
        return _empty_order_block()

    i = best["i"]
    zl, zh = best["zone_low"], best["zone_high"]
    price = float(closes[-1])

    # Ignore the displacement candles themselves when counting later tests.
    touches = 0
    mitigated = False
    invalidated = False
    for j in range(i + 4, n):
        overlaps = lows[j] <= zh and highs[j] >= zl
        if overlaps:
            touches += 1
        if closes[j] < zl:
            invalidated = True
            break
        if overlaps:
            mitigated = True

    in_zone = zl <= price <= zh
    above_zone = price > zh
    distance_pct = abs(price / zh - 1.0) * 100.0 if zh else 999.0
    zone_width_pct = max(0.01, (zh - zl) / zh * 100.0)

    if invalidated:
        status = "INVALIDATED"
    elif in_zone and touches > 0:
        status = "PARTIALLY_MITIGATED"
    elif touches > 0:
        status = "TESTED"
    else:
        status = "FRESH"

    # Freshness decays with each revisit; a fresh zone gets the full allocation.
    freshness_score = max(0.0, 100.0 - touches * 25.0)
    if in_zone:
        timing = "OPTIMAL"
    elif above_zone and distance_pct <= max(1.0, zone_width_pct * 2.0):
        timing = "EARLY"
    elif above_zone and distance_pct <= max(2.5, zone_width_pct * 5.0):
        timing = "LATE"
    else:
        timing = "INVALID"

    quality = (
        displacement_score * 0.30
        + volume_score * 0.15
        + bos_score * 0.20
        + freshness_score * 0.20
        + (10.0 if best["liquidity_sweep"] else 0.0)
        + (10.0 if timing in {"EARLY", "OPTIMAL"} else 0.0)
        + (5.0 if in_zone else 0.0)
    )
    if invalidated:
        quality = 0.0
    quality = max(0.0, min(100.0, quality))

    midpoint = (zl + zh) / 2.0
    return {
        "bullish": True,
        "fresh": status == "FRESH",
        "zone_low": round(zl, 8),
        "zone_high": round(zh, 8),
        "midpoint": round(midpoint, 8),
        "distance_pct": round(distance_pct, 3),
        "strength": round(quality, 1),
        "quality_score": round(quality, 1),
        "status": status,
        "timing": timing,
        "touches": touches,
        "displacement_score": round(displacement_score, 1),
        "volume_score": round(volume_score, 1),
        "bos_score": round(bos_score, 1),
        "liquidity_sweep": bool(best["liquidity_sweep"]),
        "invalidation": round(zl, 8),
        "confluence": "LOCAL",
        "timeframe_context": "local",
    }


def _volatility(highs,lows,closes,atr):
    """ATR regime: magnitude/context only, never direction."""
    if not closes or atr<=0: return {"atr":0.0,"atr_pct":0.0,"atr_ratio":1.0,"percentile":50.0,"state":"UNKNOWN","expansion":False}
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
    current=mean(trs[-14:]) if len(trs)>=14 else atr
    history=[mean(trs[i-13:i+1]) for i in range(13,len(trs))]
    baseline=mean(history[-50:]) if history else current
    rank=sum(1 for x in history if x<=current)/len(history)*100 if history else 50
    ratio=current/baseline if baseline>0 else 1
    state="EXPANDING" if ratio>=1.12 else "CONTRACTING" if ratio<=0.88 else "NORMAL"
    return {"atr":round(current,8),"atr_pct":round(current/closes[-1]*100,3),"atr_ratio":round(ratio,3),"percentile":round(rank,1),"state":state,"expansion":state=="EXPANDING"}


def build_signal_context(closes,highs,lows,vols,atr,price):
    return {"zero_inverse":_zero_inverse(closes),"order_block":_order_block(closes,highs,lows,vols,atr),"volatility":_volatility(highs,lows,closes,atr)}
