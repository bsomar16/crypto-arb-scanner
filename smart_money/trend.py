"""Smart Money Bullish Trend & Momentum Structure Engine for Binance Spot.

Identifies institutional-backed bullish trends and healthy trend pullbacks while
strictly filtering out overextended parabolic chases and weak bearish bounces.

Evaluates:
- EMA trend stack (EMA9, EMA20, EMA50) and EMA20 directional slope
- Wilder ADX(14), +DI(14), and -DI(14) directional strength
- Causal swing-high / swing-low (Higher Highs & Higher Lows) structure
- Wilder RSI(14) healthy bullish momentum band & overextension protection
- Smart Money Volume/CVD orderflow confirmation (from volume_cvd.py)
- Optional Higher-Timeframe (e.g. 4h) trend alignment confluence
- Classification: STRONG_BULLISH_TREND, BULLISH_TREND, BULLISH_PULLBACK, OVEREXTENDED_BULL, NEUTRAL, BEARISH

Interface Contract:
- Closed Binance Spot candles only (`as_of_ms` filters unclosed bars).
- Pure analytical computation — zero network calls, zero order placement.
- Strictly Binance Spot Long/BUY context only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .volume_cvd import (
    VolumeCvdResult,
    _atr_series,
    _extract_candle,
    compute_volume_cvd_analytics,
)


@dataclass(frozen=True)
class BullishTrendResult:
    """Immutable result of Bullish Trend & Momentum analytical evaluation."""
    trend_score: float
    trend_state: str  # STRONG_BULLISH_TREND, BULLISH_TREND, BULLISH_PULLBACK, OVEREXTENDED_BULL, NEUTRAL, BEARISH
    close: float
    ema9: float
    ema20: float
    ema50: float
    ema20_slope_pct: float
    adx14: float
    plus_di14: float
    minus_di14: float
    rsi14: float
    atr14: float
    dist_ema20_atr: float
    pct_24h: float
    higher_highs: bool
    higher_lows: bool
    overextended: bool
    htf_aligned: Optional[bool]
    suggested_stop: float
    factor_scores: Dict[str, float]
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trend_score": round(self.trend_score, 1),
            "trend_state": self.trend_state,
            "close": round(self.close, 6),
            "ema9": round(self.ema9, 6),
            "ema20": round(self.ema20, 6),
            "ema50": round(self.ema50, 6),
            "ema20_slope_pct": round(self.ema20_slope_pct, 3),
            "adx14": round(self.adx14, 2),
            "plus_di14": round(self.plus_di14, 2),
            "minus_di14": round(self.minus_di14, 2),
            "rsi14": round(self.rsi14, 2),
            "atr14": round(self.atr14, 6),
            "dist_ema20_atr": round(self.dist_ema20_atr, 2),
            "pct_24h": round(self.pct_24h, 2),
            "higher_highs": self.higher_highs,
            "higher_lows": self.higher_lows,
            "overextended": self.overextended,
            "htf_aligned": self.htf_aligned,
            "suggested_stop": round(self.suggested_stop, 6),
            "factor_scores": {k: round(v, 3) for k, v in self.factor_scores.items()},
            "reasons": list(self.reasons),
        }


def _ema_series(values: Sequence[float], period: int) -> List[float]:
    """Compute standard exponential moving average series."""
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1.0 - k))
    return out


def _wilder_rsi14(closes: Sequence[float], period: int = 14) -> float:
    """Compute Wilder RSI(14) over closed bars."""
    n = len(closes)
    if n < period + 1:
        return 50.0
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 1e-12 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _wilder_dmi_adx14(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Tuple[float, float, float]:
    """Compute Wilder ADX(14), +DI(14), and -DI(14)."""
    n = len(closes)
    if n < period * 2 + 1:
        return 0.0, 0.0, 0.0

    trs: List[float] = []
    plus_dms: List[float] = []
    minus_dms: List[float] = []

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm = up_move if (up_move > down_move and up_move > 0.0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0.0) else 0.0
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    sm_tr = sum(trs[:period])
    sm_plus = sum(plus_dms[:period])
    sm_minus = sum(minus_dms[:period])

    dx_list: List[float] = []
    last_plus_di = 0.0
    last_minus_di = 0.0

    for i in range(period, len(trs)):
        if i > period:
            sm_tr = sm_tr - (sm_tr / period) + trs[i]
            sm_plus = sm_plus - (sm_plus / period) + plus_dms[i]
            sm_minus = sm_minus - (sm_minus / period) + minus_dms[i]

        if sm_tr <= 1e-12:
            pdi = 0.0
            mdi = 0.0
        else:
            pdi = 100.0 * (sm_plus / sm_tr)
            mdi = 100.0 * (sm_minus / sm_tr)

        last_plus_di = pdi
        last_minus_di = mdi
        denom = pdi + mdi
        dx = (100.0 * abs(pdi - mdi) / denom) if denom > 1e-12 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return 0.0, last_plus_di, last_minus_di

    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period

    return adx, last_plus_di, last_minus_di


def _detect_swing_structure(
    highs: Sequence[float],
    lows: Sequence[float],
    window: int = 36,
) -> Tuple[bool, bool, Optional[float]]:
    """Detect causal 3-bar swing Higher-Highs, Higher-Lows, and most recent swing low."""
    n = len(highs)
    if n < 8:
        return False, False, None

    start = max(1, n - window)
    swing_highs: List[float] = []
    swing_lows: List[float] = []

    # Causal pivots: bar j confirmed strictly by bar j+1 (j <= n - 2)
    for j in range(start, n - 1):
        if highs[j] > highs[j - 1] and highs[j] >= highs[j + 1]:
            swing_highs.append(highs[j])
        if lows[j] < lows[j - 1] and lows[j] <= lows[j + 1]:
            swing_lows.append(lows[j])

    hh = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
    hl = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
    last_swing_low = swing_lows[-1] if swing_lows else None
    return hh, hl, last_swing_low


def evaluate_bullish_trend(
    candles: Sequence[Any],
    cvd_result: Optional[VolumeCvdResult] = None,
    htf_candles: Optional[Sequence[Any]] = None,
    as_of_ms: Optional[int] = None,
) -> BullishTrendResult:
    """Evaluate Smart Money Bullish Trend & Momentum Structure on closed candles.

    Args:
        candles: Sequence of closed Binance Spot klines (e.g. 1h, >= 55 bars recommended).
        cvd_result: Precomputed VolumeCvdResult (computed automatically if None).
        htf_candles: Optional higher-timeframe closed klines (e.g. 4h) for trend alignment.
        as_of_ms: Optional evaluation timestamp (ms) enforcing closed-candle causality.

    Returns:
        BullishTrendResult with score (0-100), classification, indicators, stop level, and reasons.
    """
    parsed = []
    for c in candles:
        item, _ = _extract_candle(c, as_of_ms=as_of_ms)
        if item is not None:
            parsed.append(item)

    n = len(parsed)
    if n < 35:
        return BullishTrendResult(
            trend_score=0.0,
            trend_state="NEUTRAL",
            close=0.0,
            ema9=0.0,
            ema20=0.0,
            ema50=0.0,
            ema20_slope_pct=0.0,
            adx14=0.0,
            plus_di14=0.0,
            minus_di14=0.0,
            rsi14=50.0,
            atr14=0.0,
            dist_ema20_atr=0.0,
            pct_24h=0.0,
            higher_highs=False,
            higher_lows=False,
            overextended=False,
            htf_aligned=None,
            suggested_stop=0.0,
            factor_scores={},
            reasons=["Insufficient candle history for trend evaluation (<35 bars)"],
        )

    if cvd_result is None:
        cvd_result = compute_volume_cvd_analytics(candles, as_of_ms=as_of_ms, lookback=min(40, n))

    highs = [p[3] for p in parsed]
    lows = [p[4] for p in parsed]
    closes = [p[5] for p in parsed]

    close_now = closes[-1]
    ema9_s = _ema_series(closes, 9)
    ema20_s = _ema_series(closes, 20)
    ema50_s = _ema_series(closes, 50)

    ema9 = ema9_s[-1]
    ema20 = ema20_s[-1]
    ema50 = ema50_s[-1]

    slope_ref = ema20_s[-6] if len(ema20_s) >= 6 else ema20_s[0]
    ema20_slope_pct = ((ema20 - slope_ref) / slope_ref * 100.0) if slope_ref > 1e-12 else 0.0

    atrs = _atr_series(highs, lows, closes, period=14)
    atr14 = atrs[-1] if atrs and atrs[-1] > 1e-12 else max(close_now * 0.01, 1e-9)

    rsi14 = _wilder_rsi14(closes, period=14)
    adx14, plus_di14, minus_di14 = _wilder_dmi_adx14(highs, lows, closes, period=14)
    higher_highs, higher_lows, last_swing_low = _detect_swing_structure(highs, lows, window=36)

    dist_ema20_atr = (close_now - ema20) / atr14 if atr14 > 1e-12 else 0.0
    dist_ema50_atr = (close_now - ema50) / atr14 if atr14 > 1e-12 else 0.0
    ref_24h = closes[-25] if len(closes) >= 25 else closes[0]
    pct_24h = ((close_now - ref_24h) / ref_24h * 100.0) if ref_24h > 1e-12 else 0.0
    ref_48h = closes[-49] if len(closes) >= 49 else closes[0]
    pct_48h = ((close_now - ref_48h) / ref_48h * 100.0) if ref_48h > 1e-12 else 0.0

    # Higher-timeframe (e.g. 4h) alignment check
    htf_aligned: Optional[bool] = None
    htf_bearish = False
    if htf_candles:
        htf_parsed = []
        for hc in htf_candles:
            hitem, _ = _extract_candle(hc, as_of_ms=as_of_ms)
            if hitem is not None:
                htf_parsed.append(hitem)
        if len(htf_parsed) >= 35:
            h_highs = [p[3] for p in htf_parsed]
            h_lows = [p[4] for p in htf_parsed]
            h_closes = [p[5] for p in htf_parsed]
            h_e20 = _ema_series(h_closes, 20)[-1]
            h_e50 = _ema_series(h_closes, 50)[-1]
            _, h_pdi, h_mdi = _wilder_dmi_adx14(h_highs, h_lows, h_closes, period=14)
            if h_closes[-1] > h_e20 and h_e20 >= h_e50 and h_pdi >= h_mdi:
                htf_aligned = True
            elif h_closes[-1] > h_e20:
                htf_aligned = True
            else:
                htf_aligned = False
                if h_closes[-1] < h_e50 and h_e20 < h_e50:
                    htf_bearish = True

    # Anti-chasing overextension guard (prevents buying parabolic blow-off tops or rolled 48h spikes)
    overextended = (
        dist_ema20_atr > 3.0
        or dist_ema50_atr > 4.8
        or rsi14 > 76.0
        or pct_24h > 28.0
        or pct_48h > 40.0
    )

    reasons: List[str] = []
    factors: Dict[str, float] = {}

    # 1. EMA Alignment & Slope (25%)
    if close_now > ema20 > ema50 and ema20_slope_pct > 0.05:
        factors["ema_alignment"] = 1.0
        reasons.append(f"Bullish EMA stack (Price > EMA20 > EMA50, slope {ema20_slope_pct:+.2f}%)")
    elif close_now > ema20 > ema50:
        factors["ema_alignment"] = 0.75
        reasons.append("Bullish EMA alignment (Price > EMA20 > EMA50)")
    elif close_now > ema50 and ema20 > ema50 and dist_ema20_atr >= -0.6:
        factors["ema_alignment"] = 0.65
        reasons.append("Uptrend pullback testing EMA20 support above EMA50")
    elif close_now > ema20 and ema20_slope_pct > 0.0:
        factors["ema_alignment"] = 0.40
    else:
        factors["ema_alignment"] = 0.0

    # 2. Directional Trend Strength via Wilder ADX14 (20%)
    if plus_di14 > minus_di14:
        if adx14 >= 25.0:
            factors["adx_strength"] = 1.0
            reasons.append(f"Strong bullish trend (ADX {adx14:.1f}, +DI {plus_di14:.1f} > -DI {minus_di14:.1f})")
        elif adx14 >= 20.0:
            factors["adx_strength"] = 0.80
            reasons.append(f"Confirmed bullish trend (ADX {adx14:.1f}, +DI > -DI)")
        elif adx14 >= 15.0:
            factors["adx_strength"] = 0.50
        else:
            factors["adx_strength"] = 0.20
    else:
        factors["adx_strength"] = 0.0

    # 3. Swing Structure: Higher Highs & Higher Lows (15%)
    if higher_highs and higher_lows:
        factors["swing_structure"] = 1.0
        reasons.append("Confirmed Higher Highs & Higher Lows swing structure")
    elif higher_lows:
        factors["swing_structure"] = 0.65
        reasons.append("Ascending Higher Lows support structure")
    elif higher_highs:
        factors["swing_structure"] = 0.45
    else:
        factors["swing_structure"] = 0.0

    # 4. Smart Money CVD & Orderflow Confirmation (20%)
    flow_score = 0.0
    if cvd_result.cvd_bullish_divergence or cvd_result.absorption_detected:
        flow_score += 0.50
    if cvd_result.cvd_slope_normalized >= 0.06:
        flow_score += 0.55
        reasons.append(f"Strong institutional buying flow (CVD slope {cvd_result.cvd_slope_normalized:+.3f})")
    elif cvd_result.cvd_slope_normalized > 0.0:
        flow_score += 0.35
        reasons.append(f"Positive Smart Money CVD slope ({cvd_result.cvd_slope_normalized:+.3f})")
    if cvd_result.taker_buy_ratio >= 0.54:
        flow_score += 0.30
    elif cvd_result.taker_buy_ratio >= 0.51:
        flow_score += 0.15
    if cvd_result.rvol >= 1.25:
        flow_score += 0.20
    factors["smart_money_flow"] = min(1.0, round(flow_score, 3))

    # 5. Entry Zone & Momentum Health (10%)
    if overextended:
        factors["entry_zone"] = 0.0
        reasons.append(
            f"Overextended (EMA20 dist {dist_ema20_atr:.1f} ATR, RSI {rsi14:.1f}, 24h {pct_24h:+.1f}%) — wait for pullback"
        )
    elif -0.5 <= dist_ema20_atr <= 2.0 and 45.0 <= rsi14 <= 70.0:
        factors["entry_zone"] = 1.0
        reasons.append(f"Healthy entry zone ({dist_ema20_atr:+.2f} ATR from EMA20, RSI {rsi14:.1f})")
    elif -0.8 <= dist_ema20_atr <= 2.6 and 42.0 <= rsi14 <= 74.0:
        factors["entry_zone"] = 0.55
    else:
        factors["entry_zone"] = 0.15

    # 6. Higher-Timeframe Confluence (10%)
    if htf_aligned is True:
        factors["htf_confluence"] = 1.0
        reasons.append("4h higher-timeframe bullish alignment confirmed")
    elif htf_aligned is None:
        factors["htf_confluence"] = 0.5
    else:
        factors["htf_confluence"] = 0.0

    weights = {
        "ema_alignment": 25.0,
        "adx_strength": 20.0,
        "swing_structure": 15.0,
        "smart_money_flow": 20.0,
        "entry_zone": 10.0,
        "htf_confluence": 10.0,
    }
    trend_score = sum(factors[k] * weights[k] for k in weights)
    trend_score = max(0.0, min(100.0, trend_score))

    # Structural stop-loss placement for bullish trend setups
    raw_stop = ema20 - (1.0 * atr14)
    if last_swing_low is not None and last_swing_low < close_now:
        raw_stop = min(raw_stop, last_swing_low - (0.25 * atr14))
    # Clamp stop distance to sensible [1.0 ATR, 2.5 ATR] below entry
    min_stop = close_now - (2.5 * atr14)
    max_stop = close_now - (1.0 * atr14)
    suggested_stop = max(min_stop, min(max_stop, raw_stop))
    if suggested_stop <= 0.0:
        suggested_stop = close_now * 0.97

    # Classification hierarchy
    if close_now < ema50 and ema20 < ema50 and minus_di14 > plus_di14:
        trend_state = "BEARISH"
    elif overextended and close_now > ema20 > ema50:
        trend_state = "OVEREXTENDED_BULL"
    elif (
        trend_score >= 72.0
        and close_now > ema20 > ema50
        and plus_di14 > minus_di14
        and adx14 >= 20.0
        and factors["smart_money_flow"] >= 0.40
        and not htf_bearish
    ):
        trend_state = "STRONG_BULLISH_TREND"
    elif (
        trend_score >= 58.0
        and close_now > ema50
        and ema20 > ema50
        and -0.6 <= dist_ema20_atr <= 0.8
        and higher_lows
        and (factors["smart_money_flow"] >= 0.35 or cvd_result.absorption_detected)
        and not htf_bearish
    ):
        trend_state = "BULLISH_PULLBACK"
    elif (
        trend_score >= 62.0
        and close_now > ema20 > ema50
        and plus_di14 > minus_di14
        and adx14 >= 16.0
        and not htf_bearish
    ):
        trend_state = "BULLISH_TREND"
    else:
        trend_state = "NEUTRAL"

    return BullishTrendResult(
        trend_score=trend_score,
        trend_state=trend_state,
        close=close_now,
        ema9=ema9,
        ema20=ema20,
        ema50=ema50,
        ema20_slope_pct=ema20_slope_pct,
        adx14=adx14,
        plus_di14=plus_di14,
        minus_di14=minus_di14,
        rsi14=rsi14,
        atr14=atr14,
        dist_ema20_atr=dist_ema20_atr,
        pct_24h=pct_24h,
        higher_highs=higher_highs,
        higher_lows=higher_lows,
        overextended=overextended,
        htf_aligned=htf_aligned,
        suggested_stop=suggested_stop,
        factor_scores=factors,
        reasons=reasons,
    )
