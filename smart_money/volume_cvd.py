"""Volume Intelligence & Cumulative Volume Delta (CVD) Engine for Binance Spot.

Calculates:
- Realized Volume Delta (Taker Buy vs Taker Sell) from raw Binance klines
- Continuous Cumulative Volume Delta (CVD) without window-boundary jumps
- CVD Slope using Ordinary Least Squares (OLS) linear regression
- Volume Z-Score and Relative Volume (RVOL)
- Multi-Condition Selling Pressure Absorption
- Structural Price/CVD Bullish Divergence (paired swing lows)
- Closed-Candle Enforcement and Mandatory Data Confidence Scoring

Interface Contract:
- Input candles must represent confirmed closed Binance Spot klines.
- When `as_of_ms` is supplied, the engine strictly enforces `close_time < as_of_ms`
  and discards active/unclosed candles.
- Strictly Binance Spot Long/BUY analytical context only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class VolumeCvdResult:
    """Immutable result of Volume and CVD analytical calculations."""
    delta: float
    delta_norm: float
    taker_buy_ratio: float
    cvd_series: List[float]
    cvd_latest: float
    cvd_slope: float
    cvd_slope_normalized: float
    rvol: float
    vol_zscore: float
    absorption_detected: bool
    absorption_reasons: List[str]
    cvd_bullish_divergence: bool
    divergence_bars: int
    data_confidence: float
    data_quality_flags: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta": round(self.delta, 4),
            "delta_norm": round(self.delta_norm, 4),
            "taker_buy_ratio": round(self.taker_buy_ratio, 4),
            "cvd_latest": round(self.cvd_latest, 4),
            "cvd_slope": round(self.cvd_slope, 4),
            "cvd_slope_normalized": round(self.cvd_slope_normalized, 4),
            "rvol": round(self.rvol, 2),
            "vol_zscore": round(self.vol_zscore, 2),
            "absorption_detected": self.absorption_detected,
            "absorption_reasons": list(self.absorption_reasons),
            "cvd_bullish_divergence": self.cvd_bullish_divergence,
            "divergence_bars": self.divergence_bars,
            "data_confidence": round(self.data_confidence, 1),
            "data_quality_flags": dict(self.data_quality_flags),
        }


def _extract_candle(
    candle: Any,
    as_of_ms: Optional[int] = None,
) -> Tuple[Optional[Tuple[int, int, float, float, float, float, float, float, int]], Optional[str]]:
    """Extract and validate (open_time, close_time, open, high, low, close, volume, taker_buy_vol, num_trades).

    Validations:
    - Positive prices: open, high, low, close > 0
    - OHLC relationship: high >= max(open, close) and low <= min(open, close)
    - Volume non-negative: volume >= 0
    - Taker volume presence & bounds: taker_buy_base_volume must be present and 0 <= taker_buy <= volume
    - Timestamp > 0
    - Closed candle check: If as_of_ms is provided, close_time < as_of_ms (or open_time < as_of_ms)

    Returns:
        (candle_tuple, rejection_reason)
    """
    try:
        t: int = 0
        ct: int = 0
        o: float = 0.0
        h: float = 0.0
        l: float = 0.0
        c: float = 0.0
        v: float = 0.0
        v_tb_raw: Any = None
        n_trades: int = 0

        if isinstance(candle, dict):
            t_raw = candle.get("t", candle.get("open_time"))
            if t_raw is None:
                return None, "missing open_time"
            t = int(t_raw)
            ct_raw = candle.get("ct", candle.get("close_time", candle.get("close_ms")))
            ct = int(ct_raw) if ct_raw is not None else 0

            o_raw = candle.get("o", candle.get("open"))
            h_raw = candle.get("h", candle.get("high"))
            l_raw = candle.get("l", candle.get("low"))
            c_raw = candle.get("c", candle.get("close"))
            v_raw = candle.get("v", candle.get("volume"))

            if any(x is None for x in (o_raw, h_raw, l_raw, c_raw, v_raw)):
                return None, "missing core OHLCV field in candle dict"

            o = float(o_raw)
            h = float(h_raw)
            l = float(l_raw)
            c = float(c_raw)
            v = float(v_raw)

            # Binance taker buy base volume column is column 9 (taker_buy_base_volume or v_tb)
            # NOTE: q_tb (quote volume) is explicitly rejected as base taker volume
            if "v_tb" in candle:
                v_tb_raw = candle["v_tb"]
            elif "taker_buy_base_volume" in candle:
                v_tb_raw = candle["taker_buy_base_volume"]
            else:
                return None, "missing taker_buy_base_volume (q_tb is not base volume)"

            n_trades = int(candle.get("n_trades", candle.get("trades", candle.get("number_of_trades", 0))))

        elif isinstance(candle, (list, tuple)):
            if len(candle) < 10:
                return None, f"candle length {len(candle)} < 10 (requires column 9 taker_buy_base_volume)"
            t = int(candle[0])
            o = float(candle[1])
            h = float(candle[2])
            l = float(candle[3])
            c = float(candle[4])
            v = float(candle[5])
            ct = int(candle[6]) if len(candle) >= 7 else 0
            n_trades = int(candle[8]) if len(candle) >= 9 else 0
            v_tb_raw = candle[9]
        else:
            return None, f"unsupported candle type {type(candle).__name__}"

        if v_tb_raw is None:
            return None, "taker_buy_base_volume is None"

        v_tb = float(v_tb_raw)

        # 1. Timestamp validation
        if t <= 0:
            return None, f"invalid open_time: {t}"

        # 2. Closed candle verification against as_of_ms
        if as_of_ms is not None:
            if ct > 0 and ct >= as_of_ms:
                return None, f"candle not closed (close_time {ct} >= as_of_ms {as_of_ms})"
            elif ct == 0 and t >= as_of_ms:
                return None, f"candle open_time {t} >= as_of_ms {as_of_ms}"

        # 3. Price positivity
        if o <= 0.0 or h <= 0.0 or l <= 0.0 or c <= 0.0:
            return None, f"non-positive price in OHLC (o={o}, h={h}, l={l}, c={c})"

        # 4. OHLC structural bounds
        if h < max(o, c) - 1e-9:
            return None, f"high {h} < max(open={o}, close={c})"
        if l > min(o, c) + 1e-9:
            return None, f"low {l} > min(open={o}, close={c})"

        # 5. Volume non-negativity
        if v < 0.0:
            return None, f"negative total volume: {v}"

        # 6. Taker volume bounds [0, volume]
        if v_tb < -1e-9:
            return None, f"negative taker buy volume: {v_tb}"
        if v_tb > v + 1e-7:
            return None, f"taker buy volume {v_tb} exceeds total volume {v}"

        v_tb = max(0.0, min(v, v_tb))
        return (t, ct, o, h, l, c, v, v_tb, n_trades), None

    except (ValueError, TypeError, IndexError) as err:
        return None, f"parse error: {err}"


def cvd_linear_regression_slope(series: Sequence[float], norm_baseline: Optional[float] = None) -> Tuple[float, float]:
    """Calculate the ordinary least squares linear regression slope of a series.

    Returns:
        (raw_slope, normalized_slope)
    """
    n = len(series)
    if n < 3:
        return 0.0, 0.0

    mean_x = (n - 1) / 2.0
    mean_y = sum(series) / n
    var_x = (n * (n * n - 1.0)) / 12.0

    cov_xy = sum(i * y for i, y in enumerate(series)) - (n * mean_x * mean_y)
    if var_x <= 1e-12:
        return 0.0, 0.0

    raw_slope = cov_xy / var_x
    baseline = max(abs(norm_baseline or 1.0), 1e-9)
    norm_slope = raw_slope / baseline
    return raw_slope, norm_slope


def _atr_series(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> List[float]:
    """Calculate standard Wilder's ATR series across closed candles."""
    n = len(closes)
    if n < 2:
        return [0.0] * n

    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        h, l, prev_c = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))

    if len(tr) < period:
        return [mean(tr)] * n

    atr = [mean(tr[:period])]
    for i in range(period, n):
        atr.append((atr[-1] * (period - 1) + tr[i]) / period)

    padding = [atr[0]] * (period - 1)
    return padding + atr


def _detect_bullish_divergence(
    lows: Sequence[float],
    highs: Sequence[float],
    cvd_series: Sequence[float],
    window: int = 30,
) -> Tuple[bool, int]:
    """Detect confirmed structural price/CVD bullish divergence with paired swing points.

    Algorithm:
    1. Examines trailing window of closed bars.
    2. Identifies earlier structural pivot low (Trough 1) at bar i1.
    3. Identifies recent structural pivot low (Trough 2) at bar i2.
    4. Enforces minimum separation (i2 - i1 >= 3 bars) and an intervening price bounce.
    5. Divergence confirmed if Price(i2) <= Price(i1) * 1.002 while CVD(i2) > CVD(i1).

    Returns:
        (divergence_detected, divergence_bars_span)
    """
    n = len(lows)
    eff_w = min(window, n)
    if eff_w < 10:
        return False, 0

    sub_lows = lows[-eff_w:]
    sub_highs = highs[-eff_w:]
    sub_cvd = cvd_series[-eff_w:]
    m = len(sub_lows)

    # Search for local swing lows (troughs)
    # A swing low at index k has sub_lows[k] <= sub_lows[k-1] and sub_lows[k] <= sub_lows[k+1]
    pivots: List[int] = []
    for k in range(1, m - 1):
        if sub_lows[k] <= sub_lows[k - 1] and sub_lows[k] <= sub_lows[k + 1]:
            pivots.append(k)

    if len(pivots) < 2:
        # Fallback: check lowest point in earlier half vs lowest point in last 4 bars
        i1 = int(min(range(1, max(2, m - 4)), key=lambda idx: sub_lows[idx]))
        i2 = int(min(range(max(i1 + 3, m - 4), m), key=lambda idx: sub_lows[idx])) if (m - 4) > i1 + 2 else -1
        if i2 == -1:
            return False, 0
    else:
        # Use last two distinct pivot lows
        i2 = pivots[-1]
        i1 = pivots[-2]
        # If pivots are too close, search further back
        if (i2 - i1 < 3) and len(pivots) >= 3:
            i1 = pivots[-3]

    if i2 - i1 < 3:
        return False, 0

    p1 = sub_lows[i1]
    p2 = sub_lows[i2]
    c1 = sub_cvd[i1]
    c2 = sub_cvd[i2]

    # Verify intervening swing bounce between i1 and i2
    bounce_high = max(sub_highs[i1:i2 + 1])
    min_trough = min(p1, p2)
    if bounce_high < min_trough * 1.002:
        return False, 0

    # Bullish divergence: Price tested or made lower low, but CVD printed higher low
    price_tested_or_lower = p2 <= p1 * 1.002
    cvd_strictly_higher = c2 > c1 + (0.01 * max(abs(c1), 1.0))

    if price_tested_or_lower and cvd_strictly_higher:
        return True, eff_w - i1

    return False, 0


def compute_volume_cvd_analytics(
    candles: Sequence[Any],
    as_of_ms: Optional[int] = None,
    lookback: int = 50,
    slope_window: int = 20,
    divergence_lookback: int = 30,
) -> VolumeCvdResult:
    """Compute Volume Delta, continuous CVD, Absorption, and Divergence from closed candles.

    Args:
        candles: Sequence of closed Binance Spot kline dicts or tuples.
        as_of_ms: Optional evaluation timestamp (ms) to strictly enforce closed candle guarantee.
        lookback: Length of trailing window for CVD and volume stats.
        slope_window: Window size for OLS CVD slope calculation.
        divergence_lookback: Window size for checking price/CVD swing divergence.

    Returns:
        VolumeCvdResult dataclass with metrics and data hygiene flags.
    """
    parsed: List[Tuple[int, int, float, float, float, float, float, float, int]] = []
    rejected_reasons: List[str] = []

    for c in candles:
        item, err = _extract_candle(c, as_of_ms=as_of_ms)
        if item is not None:
            parsed.append(item)
        else:
            if err:
                rejected_reasons.append(err)

    n = len(parsed)
    flags: Dict[str, Any] = {
        "sufficient_history": n >= 30,
        "taker_volume_present": False,
        "taker_volume_valid": True,
        "timestamps_strictly_increasing": True,
        "valid_ohlcv": len(rejected_reasons) == 0,
        "closed_candles_verified": as_of_ms is not None,
        "rejected_candles_count": len(rejected_reasons),
    }

    timestamps = [p[0] for p in parsed]
    taker_buys = [p[7] for p in parsed]
    volumes = [p[6] for p in parsed]

    # Timestamp monotonicity and duplicate checks (evaluated across all parsed bars)
    for i in range(1, n):
        if timestamps[i] <= timestamps[i - 1]:
            flags["timestamps_strictly_increasing"] = False
            break

    # Taker volume presence check
    total_taker_buys = sum(taker_buys)
    total_volume = sum(volumes)
    if total_volume > 0 and total_taker_buys > 0:
        flags["taker_volume_present"] = True

    if n < 5:
        confidence = max(0.0, 40.0 - (5 - n) * 10.0)
        if not flags["timestamps_strictly_increasing"]:
            confidence -= 30.0
        return VolumeCvdResult(
            delta=0.0,
            delta_norm=0.0,
            taker_buy_ratio=0.5,
            cvd_series=[0.0] if n == 0 else [0.0] * n,
            cvd_latest=0.0,
            cvd_slope=0.0,
            cvd_slope_normalized=0.0,
            rvol=1.0,
            vol_zscore=0.0,
            absorption_detected=False,
            absorption_reasons=[],
            cvd_bullish_divergence=False,
            divergence_bars=0,
            data_confidence=max(0.0, confidence),
            data_quality_flags=flags,
        )

    opens = [p[2] for p in parsed]
    highs = [p[3] for p in parsed]
    lows = [p[4] for p in parsed]
    closes = [p[5] for p in parsed]

    # 1. Chronological Cumulative Volume Delta calculation across ALL candles
    deltas: List[float] = []
    cvd_full: List[float] = []
    cum = 0.0

    for i in range(n):
        v = volumes[i]
        v_tb = taker_buys[i]
        # Delta = aggressive buys - aggressive sells = 2 * v_tb - v
        delta = 2.0 * v_tb - v
        deltas.append(delta)
        cum += delta
        cvd_full.append(cum)

    latest_vol = volumes[-1]
    latest_vtb = taker_buys[-1]
    latest_delta = deltas[-1]
    delta_norm = (latest_delta / latest_vol) if latest_vol > 1e-9 else 0.0
    delta_norm = max(-1.0, min(1.0, delta_norm))
    taker_buy_ratio = (latest_vtb / latest_vol) if latest_vol > 1e-9 else 0.5

    # Lookback slice for trailing CVD
    eff_lookback = min(lookback, n)
    cvd_window = cvd_full[-eff_lookback:]
    latest_cvd = cvd_full[-1]

    # 2. CVD Slope via Linear Regression (OLS) over trailing slope_window
    eff_slope_w = min(slope_window, n)
    slope_slice = cvd_full[-eff_slope_w:]
    avg_vol_slope = mean(volumes[-eff_slope_w:]) if eff_slope_w > 0 else 1.0
    raw_slope, norm_slope = cvd_linear_regression_slope(slope_slice, norm_baseline=avg_vol_slope)

    # 3. RVOL and Volume Z-Score over trailing 20 bars
    stat_len = min(20, n - 1)
    if stat_len >= 5:
        base_vols = volumes[-(stat_len + 1):-1]
        mean_vol = mean(base_vols)
        rvol = (latest_vol / mean_vol) if mean_vol > 1e-9 else 1.0
        sd_vol = stdev(base_vols) if len(base_vols) > 1 else 0.0
        vol_zscore = ((latest_vol - mean_vol) / sd_vol) if sd_vol > 1e-9 else 0.0
    else:
        rvol = 1.0
        vol_zscore = 0.0

    # 4. Multi-Condition Selling Pressure Absorption
    # Condition 1: Heavy aggressive seller volume present
    cond1_sell_pressure = (delta_norm <= -0.15) or (taker_buy_ratio <= 0.425)

    # Condition 2: Price refused to collapse (closed in upper 45% or long lower rejection wick >= 35%)
    candle_rng = highs[-1] - lows[-1]
    if candle_rng > 1e-9:
        close_pos = (closes[-1] - lows[-1]) / candle_rng
        lower_wick = (min(opens[-1], closes[-1]) - lows[-1]) / candle_rng
        cond2_price_resilience = (close_pos >= 0.45) or (lower_wick >= 0.35)
    else:
        cond2_price_resilience = False
        close_pos = 0.5
        lower_wick = 0.0

    # Condition 3: Support structure holding (low does not violate recent local support)
    atrs = _atr_series(highs, lows, closes, period=14)
    atr_val = atrs[-1] if atrs else (closes[-1] * 0.01)
    support_lookback = min(15, n - 1)
    prior_lows = lows[-(support_lookback + 1):-1]
    prior_support = min(prior_lows) if prior_lows else lows[-1]
    cond3_support_holding = lows[-1] >= (prior_support - 0.20 * atr_val)

    # Condition 4: Volume elevation (effort present)
    cond4_volume_effort = (rvol >= 1.15) or (vol_zscore >= 0.50)

    # Hard invariant: Positive delta alone is NEVER absorption
    absorption_reasons = []
    if cond1_sell_pressure and cond2_price_resilience and cond3_support_holding and cond4_volume_effort:
        absorption_detected = True
        absorption_reasons.append(f"Taker sell ratio {((1.0 - taker_buy_ratio) * 100):.1f}% absorbed")
        absorption_reasons.append(f"Resilient close pos {close_pos:.2f} / wick {lower_wick:.2f}")
        absorption_reasons.append(f"Support {prior_support:.4f} held (low {lows[-1]:.4f})")
        absorption_reasons.append(f"RVOL ×{rvol:.2f} (Z-Score {vol_zscore:+.2f})")
    else:
        absorption_detected = False

    # 5. Price / CVD Bullish Divergence Detection (Paired Swing Lows)
    cvd_bullish_divergence, divergence_bars = _detect_bullish_divergence(
        lows=lows,
        highs=highs,
        cvd_series=cvd_full,
        window=divergence_lookback,
    )

    # 6. Mandatory Data Confidence Score (0 - 100)
    confidence = 100.0
    if n < 50:
        confidence -= min(35.0, (50 - n) * 1.0)
    if not flags["taker_volume_present"]:
        confidence -= 50.0
    if not flags["timestamps_strictly_increasing"]:
        confidence -= 30.0
    if flags["rejected_candles_count"] > 0:
        confidence -= min(30.0, flags["rejected_candles_count"] * 10.0)
    if latest_vol <= 0:
        confidence -= 25.0

    data_confidence = max(0.0, min(100.0, confidence))

    return VolumeCvdResult(
        delta=latest_delta,
        delta_norm=delta_norm,
        taker_buy_ratio=taker_buy_ratio,
        cvd_series=cvd_window,
        cvd_latest=latest_cvd,
        cvd_slope=raw_slope,
        cvd_slope_normalized=norm_slope,
        rvol=rvol,
        vol_zscore=vol_zscore,
        absorption_detected=absorption_detected,
        absorption_reasons=absorption_reasons,
        cvd_bullish_divergence=cvd_bullish_divergence,
        divergence_bars=divergence_bars,
        data_confidence=data_confidence,
        data_quality_flags=flags,
    )
