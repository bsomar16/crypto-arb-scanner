"""Smart Money Wyckoff Accumulation & Base Structure Engine for Binance Spot.

Evaluates:
- Trading range consolidation boundaries (Range High, Range Low, Bandwidth %)
- Volatility compression via short-term vs long-term ATR ratio (ATR_5 / ATR_20)
- Structural Higher Lows inside valid consolidation bases (distinguishing base from runaway trend)
- Volume dry-up on pullbacks vs expansion on demand defense (with minimum observation guarantees)
- Order Book Depth Imbalance Ratio (DIR_1pct and DIR_2pct) with crossed-book and boundary validation
- Validated provisional Accumulation Scoring (0 - 100) with custom weight integrity checks
- Classification: EARLY_ACCUMULATION, PRE_BREAKOUT, BASE, NONE

Interface Contract:
- Closed Binance Spot candles only.
- When `as_of_ms` is supplied, unclosed candles are filtered out before structural evaluation.
- Strictly Binance Spot Long/BUY analytical context only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .volume_cvd import (
    VolumeCvdResult,
    _atr_series,
    _extract_candle,
    compute_volume_cvd_analytics,
)

# Configurable provisional weights for accumulation scoring (sum = 100.0).
# NOTE: All weights are provisional and subject to empirical calibration via walk-forward testing.
DEFAULT_ACCUMULATION_WEIGHTS: Dict[str, float] = {
    "base_structure": 25.0,     # Range tightness, ATR compression, duration
    "higher_lows": 20.0,        # Ascending support pivots within consolidation
    "absorption": 20.0,         # Selling pressure absorbed without structural breakdown
    "cvd_divergence": 15.0,     # Price/CVD bullish divergence or positive CVD trend
    "rvol_expansion": 10.0,     # Volume expansion on up-legs/support defense
    "volume_dryup": 10.0,       # Volume contraction/dry-up during pullbacks to support
}


def validate_accumulation_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Validate and merge accumulation scoring weights.

    Raises:
        ValueError: If weights contain unknown keys, non-numeric values, negative values,
                    or sum to <= 0.
    """
    if weights is None:
        return dict(DEFAULT_ACCUMULATION_WEIGHTS)

    valid_keys = set(DEFAULT_ACCUMULATION_WEIGHTS.keys())
    for k, v in weights.items():
        if k not in valid_keys:
            raise ValueError(f"Unknown accumulation weight key '{k}'. Valid keys are: {sorted(valid_keys)}")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"Weight for '{k}' must be numeric, got {type(v).__name__}")
        if v < 0.0:
            raise ValueError(f"Negative weight for '{k}': {v}. Weights must be non-negative.")

    merged = dict(DEFAULT_ACCUMULATION_WEIGHTS)
    merged.update(weights)

    total_weight = sum(merged.values())
    if total_weight <= 0.0:
        raise ValueError("Sum of accumulation weights must be positive (> 0.0)")

    return merged


@dataclass(frozen=True)
class AccumulationResult:
    """Immutable result of Accumulation and Order Book analytical calculations."""
    accumulation_score: float
    classification: str  # "EARLY_ACCUMULATION", "PRE_BREAKOUT", "BASE", "NONE"
    range_high: float
    range_low: float
    bandwidth_pct: float
    higher_lows: bool
    atr_compression_ratio: float
    volume_dryup: bool
    dir_1pct: float
    dir_2pct: float
    orderbook_status: str  # "ASSESSED", "UNASSESSED", "INVALID_CROSSED_BOOK", "EMPTY_DEPTH"
    factor_scores: Dict[str, float]
    factor_availability: Dict[str, bool]
    data_confidence: float
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accumulation_score": round(self.accumulation_score, 1),
            "classification": self.classification,
            "range_high": round(self.range_high, 4),
            "range_low": round(self.range_low, 4),
            "bandwidth_pct": round(self.bandwidth_pct, 2),
            "higher_lows": self.higher_lows,
            "atr_compression_ratio": round(self.atr_compression_ratio, 3),
            "volume_dryup": self.volume_dryup,
            "dir_1pct": round(self.dir_1pct, 4),
            "dir_2pct": round(self.dir_2pct, 4),
            "orderbook_status": self.orderbook_status,
            "factor_scores": {k: round(v, 3) for k, v in self.factor_scores.items()},
            "factor_availability": dict(self.factor_availability),
            "data_confidence": round(self.data_confidence, 1),
            "reasons": list(self.reasons),
        }


def calculate_orderbook_dir(
    orderbook: Optional[Dict[str, Any]],
    mid_price: float,
) -> Tuple[float, float, str]:
    """Calculate Depth Imbalance Ratio (DIR) at ±1.0% and ±2.0% price distance from mid.

    DIR = BidDepthQuote / (BidDepthQuote + AskDepthQuote)
    where DepthQuote = sum(price * qty) for levels within threshold distance.

    Validations:
    - Crossed order book detection (best_bid >= best_ask).
    - Invalid levels (non-positive prices/quantities) skipped cleanly.
    - Exact price boundaries (p == mid * (1 +/- tau)) strictly included.
    - Missing bids or asks -> UNASSESSED.
    - Zero total depth -> EMPTY_DEPTH.

    Returns:
        (dir_1pct, dir_2pct, status)
    """
    if not orderbook or mid_price <= 0:
        return 0.5, 0.5, "UNASSESSED"

    bids = orderbook.get("bids")
    asks = orderbook.get("asks")
    if not bids or not asks:
        return 0.5, 0.5, "UNASSESSED"

    # Extract valid levels and check for crossed order book
    clean_bids: List[Tuple[float, float]] = []
    clean_asks: List[Tuple[float, float]] = []

    for item in bids:
        try:
            p = float(item[0])
            q = float(item[1])
            if p > 0 and q > 0:
                clean_bids.append((p, q))
        except (ValueError, TypeError, IndexError):
            continue

    for item in asks:
        try:
            p = float(item[0])
            q = float(item[1])
            if p > 0 and q > 0:
                clean_asks.append((p, q))
        except (ValueError, TypeError, IndexError):
            continue

    if not clean_bids or not clean_asks:
        return 0.5, 0.5, "UNASSESSED"

    best_bid = max(b[0] for b in clean_bids)
    best_ask = min(a[0] for a in clean_asks)
    if best_bid >= best_ask:
        return 0.5, 0.5, "INVALID_CROSSED_BOOK"

    def _sum_quote_depth(levels: Sequence[Tuple[float, float]], is_bid: bool, pct_threshold: float) -> float:
        total = 0.0
        # Tolerances ensure exact boundaries (p == boundary) are strictly included
        if is_bid:
            boundary = mid_price * (1.0 - pct_threshold) - 1e-9
            for p, q in levels:
                if p >= boundary and p <= mid_price * 1.0001:
                    total += p * q
        else:
            boundary = mid_price * (1.0 + pct_threshold) + 1e-9
            for p, q in levels:
                if p <= boundary and p >= mid_price * 0.9999:
                    total += p * q
        return total

    bid_1pct = _sum_quote_depth(clean_bids, is_bid=True, pct_threshold=0.01)
    ask_1pct = _sum_quote_depth(clean_asks, is_bid=False, pct_threshold=0.01)
    tot_1pct = bid_1pct + ask_1pct

    bid_2pct = _sum_quote_depth(clean_bids, is_bid=True, pct_threshold=0.02)
    ask_2pct = _sum_quote_depth(clean_asks, is_bid=False, pct_threshold=0.02)
    tot_2pct = bid_2pct + ask_2pct

    if tot_1pct <= 1e-9 and tot_2pct <= 1e-9:
        return 0.5, 0.5, "EMPTY_DEPTH"

    dir_1pct = (bid_1pct / tot_1pct) if tot_1pct > 1e-9 else 0.5
    dir_2pct = (bid_2pct / tot_2pct) if tot_2pct > 1e-9 else 0.5

    return dir_1pct, dir_2pct, "ASSESSED"


def _detect_higher_lows(lows: Sequence[float], window: int) -> Tuple[bool, List[float]]:
    """Detect ascending support structure (higher structural pivot lows) across window."""
    n = len(lows)
    if n < 8:
        return False, []

    eff_w = min(window, n)
    sub_lows = list(lows[-eff_w:])
    m = len(sub_lows)

    # Find local swing pivot lows (troughs)
    pivots: List[Tuple[int, float]] = []
    for i in range(1, m - 1):
        if sub_lows[i] <= sub_lows[i - 1] and sub_lows[i] <= sub_lows[i + 1]:
            pivots.append((i, sub_lows[i]))

    if len(pivots) >= 2:
        # Require pivots to have at least 2 bars separation
        spaced_pivots: List[Tuple[int, float]] = [pivots[0]]
        for p in pivots[1:]:
            if p[0] - spaced_pivots[-1][0] >= 2:
                spaced_pivots.append(p)

        if len(spaced_pivots) >= 2:
            recent_pivots = [p[1] for p in spaced_pivots[-3:]]
            is_ascending = True
            for idx in range(1, len(recent_pivots)):
                if recent_pivots[idx] < recent_pivots[idx - 1] * 0.998:
                    is_ascending = False
                    break
            if is_ascending:
                return True, recent_pivots

    # Confluence check: split window in half and compare minimums
    half = m // 2
    first_half_min = min(sub_lows[:half])
    second_half_min = min(sub_lows[half:])
    if second_half_min >= first_half_min * 1.002:
        return True, [first_half_min, second_half_min]

    return False, []


def evaluate_accumulation_structure(
    candles: Sequence[Any],
    cvd_result: Optional[VolumeCvdResult] = None,
    weights: Optional[Dict[str, float]] = None,
    lookback: int = 40,
    orderbook: Optional[Dict[str, Any]] = None,
    as_of_ms: Optional[int] = None,
) -> AccumulationResult:
    """Evaluate Wyckoff Accumulation structure, ATR compression, and Smart Money score.

    Args:
        candles: Sequence of closed Binance Spot kline dicts or tuples.
        cvd_result: Precomputed VolumeCvdResult (computed automatically if None).
        weights: Optional dictionary overriding DEFAULT_ACCUMULATION_WEIGHTS (validated).
        lookback: Length of trailing consolidation base window.
        orderbook: Optional order book snapshot for DIR computation.
        as_of_ms: Optional evaluation timestamp (ms) to strictly enforce closed candle guarantee.

    Returns:
        AccumulationResult dataclass with scores, classifications, and audit reasons.
    """
    active_weights = validate_accumulation_weights(weights)

    parsed = []
    for c in candles:
        item, _ = _extract_candle(c, as_of_ms=as_of_ms)
        if item is not None:
            parsed.append(item)

    n = len(parsed)
    if cvd_result is None:
        cvd_result = compute_volume_cvd_analytics(candles, as_of_ms=as_of_ms, lookback=lookback)

    # Initialize factor availability tracking
    factor_availability: Dict[str, bool] = {k: True for k in active_weights}

    # Early exit on insufficient history
    if n < 10:
        dir_1, dir_2, ob_stat = calculate_orderbook_dir(orderbook, 1.0)
        return AccumulationResult(
            accumulation_score=0.0,
            classification="NONE",
            range_high=0.0,
            range_low=0.0,
            bandwidth_pct=0.0,
            higher_lows=False,
            atr_compression_ratio=1.0,
            volume_dryup=False,
            dir_1pct=dir_1,
            dir_2pct=dir_2,
            orderbook_status=ob_stat,
            factor_scores={k: 0.0 for k in active_weights},
            factor_availability={k: False for k in active_weights},
            data_confidence=cvd_result.data_confidence,
            reasons=["Insufficient candle history for accumulation structure analysis (<10 bars)"],
        )

    highs = [p[3] for p in parsed]
    lows = [p[4] for p in parsed]
    closes = [p[5] for p in parsed]
    volumes = [p[6] for p in parsed]
    opens = [p[2] for p in parsed]

    eff_lookback = min(lookback, n)
    recent_highs = highs[-eff_lookback:]
    recent_lows = lows[-eff_lookback:]
    recent_closes = closes[-eff_lookback:]
    recent_volumes = volumes[-eff_lookback:]
    recent_opens = opens[-eff_lookback:]

    range_high = max(recent_highs)
    range_low = min(recent_lows)
    latest_close = closes[-1]

    # Handle flat / zero volume / invalid trading series early
    if sum(recent_volumes) <= 1e-9 or range_high <= range_low or range_low <= 1e-9:
        dir_1, dir_2, ob_stat = calculate_orderbook_dir(orderbook, mid_price=range_low)
        return AccumulationResult(
            accumulation_score=0.0,
            classification="NONE",
            range_high=range_high,
            range_low=range_low,
            bandwidth_pct=0.0,
            higher_lows=False,
            atr_compression_ratio=1.0,
            volume_dryup=False,
            dir_1pct=dir_1,
            dir_2pct=dir_2,
            orderbook_status=ob_stat,
            factor_scores={k: 0.0 for k in active_weights},
            factor_availability={k: False for k in active_weights},
            data_confidence=cvd_result.data_confidence,
            reasons=["Zero volume or flat price series (no active trading base)"],
        )

    # 1. Trading Range Bandwidth %
    bandwidth_pct = ((range_high - range_low) / range_low * 100.0) if range_low > 1e-9 else 0.0

    # 2. Volatility (ATR) Compression Ratio (ATR_5 / ATR_20)
    atr_series_5 = _atr_series(highs, lows, closes, period=5)
    atr_series_20 = _atr_series(highs, lows, closes, period=20)
    atr_5 = atr_series_5[-1] if atr_series_5 else 0.0
    atr_20 = atr_series_20[-1] if atr_series_20 else (atr_5 or 1.0)
    atr_compression = (atr_5 / atr_20) if atr_20 > 1e-9 else 1.0

    # 3. Structural Higher Lows Detection
    has_higher_lows, hl_pivots = _detect_higher_lows(lows, window=eff_lookback)

    # 4. Volume Dry-Up on Pullbacks vs Elevation on Rallies
    MIN_OBSERVATIONS = 3
    down_vols = [recent_volumes[i] for i in range(eff_lookback) if recent_closes[i] < recent_opens[i]]
    up_vols = [recent_volumes[i] for i in range(eff_lookback) if recent_closes[i] >= recent_opens[i]]

    if len(down_vols) >= MIN_OBSERVATIONS and len(up_vols) >= MIN_OBSERVATIONS:
        mean_down_vol = mean(down_vols)
        mean_up_vol = mean(up_vols)
        mean_all_vol = mean(recent_volumes)
        volume_dryup = (mean_down_vol < mean_up_vol * 0.88) or (mean_down_vol < mean_all_vol * 0.85)
        volume_dryup_assessed = True
    else:
        volume_dryup = False
        volume_dryup_assessed = False
        factor_availability["volume_dryup"] = False

    # 5. Order Book Depth Imbalance Ratio
    dir_1pct, dir_2pct, ob_status = calculate_orderbook_dir(orderbook, mid_price=latest_close)

    # 6. Factor Scoring Calculation [0.0 - 1.0 per factor]
    factor_scores: Dict[str, float] = {}
    reasons: List[str] = []

    # Factor A: Base Structure & Tightness (0.0 - 1.0)
    if bandwidth_pct <= 6.0:
        bw_score = 1.0
    elif bandwidth_pct <= 14.0:
        bw_score = 1.0 - ((bandwidth_pct - 6.0) / (14.0 - 6.0)) * 0.5
    elif bandwidth_pct <= 22.0:
        bw_score = 0.5 - ((bandwidth_pct - 14.0) / (22.0 - 14.0)) * 0.4
    else:
        bw_score = 0.0

    comp_score = 1.0 if atr_compression <= 0.75 else (
        max(0.0, 1.0 - ((atr_compression - 0.75) / 0.50)) if atr_compression <= 1.25 else 0.0
    )
    factor_scores["base_structure"] = round(0.6 * bw_score + 0.4 * comp_score, 3)
    if bw_score >= 0.7:
        reasons.append(f"Tight consolidation base (bandwidth {bandwidth_pct:.1f}%)")
    if comp_score >= 0.7:
        reasons.append(f"Volatility compression active (ATR ratio {atr_compression:.2f})")

    # Factor B: Higher Lows Contextual Gating (0.0 - 1.0)
    # Gated by consolidation bandwidth and duration: Continuous runaway trends do not qualify as accumulation bases
    if not has_higher_lows:
        factor_scores["higher_lows"] = 0.0
    elif bandwidth_pct > 22.0:
        # Excessive bandwidth indicates open trend or volatile chop, NOT Wyckoff accumulation
        factor_scores["higher_lows"] = 0.0
        reasons.append(f"Ascending lows disqualified from accumulation (excessive bandwidth {bandwidth_pct:.1f}% > 22%)")
    elif bandwidth_pct <= 14.0:
        factor_scores["higher_lows"] = 1.0
        reasons.append("Ascending support structure confirmed inside consolidation base")
    else:
        # Moderate bandwidth (14% - 22%): Scaled factor score
        scale = 1.0 - ((bandwidth_pct - 14.0) / (22.0 - 14.0)) * 0.6
        factor_scores["higher_lows"] = round(scale, 3)
        reasons.append(f"Ascending support in wider range (bandwidth {bandwidth_pct:.1f}%)")

    # Base duration requirement
    if eff_lookback < 20 and factor_scores["higher_lows"] > 0.5:
        factor_scores["higher_lows"] = 0.5
        reasons.append(f"Limited base duration ({eff_lookback} bars < 20 bars required for full structure)")

    # Factor C: Selling Pressure Absorption (0.0 - 1.0)
    if cvd_result.absorption_detected:
        factor_scores["absorption"] = 1.0
        reasons.append("Multi-condition selling pressure absorption confirmed")
        reasons.extend([f"  • {r}" for r in cvd_result.absorption_reasons[:2]])
    else:
        factor_scores["absorption"] = 0.0

    # Factor D: CVD Divergence / Accumulation Flow (0.0 - 1.0)
    if cvd_result.cvd_bullish_divergence:
        factor_scores["cvd_divergence"] = 1.0
        reasons.append(f"Confirmed Wyckoff spring bullish divergence over {cvd_result.divergence_bars} bars")
    elif cvd_result.cvd_slope_normalized >= 0.08:
        factor_scores["cvd_divergence"] = min(0.75, cvd_result.cvd_slope_normalized * 4.0)
        reasons.append(f"Positive CVD slope (normalized {cvd_result.cvd_slope_normalized:+.3f})")
    elif cvd_result.cvd_slope_normalized > 0.0:
        factor_scores["cvd_divergence"] = 0.30
    else:
        factor_scores["cvd_divergence"] = 0.0

    # Factor E: RVOL Expansion (0.0 - 1.0)
    if cvd_result.rvol >= 1.75:
        factor_scores["rvol_expansion"] = 1.0
        reasons.append(f"Elevated relative volume (RVOL ×{cvd_result.rvol:.2f})")
    elif cvd_result.rvol >= 1.15:
        factor_scores["rvol_expansion"] = round((cvd_result.rvol - 1.15) / (1.75 - 1.15), 3)
    else:
        factor_scores["rvol_expansion"] = 0.0

    # Factor F: Volume Dry-Up (0.0 - 1.0)
    if volume_dryup_assessed:
        factor_scores["volume_dryup"] = 1.0 if volume_dryup else 0.0
        if volume_dryup:
            reasons.append("Volume dry-up on pullbacks (supply exhaustion)")
    else:
        factor_scores["volume_dryup"] = 0.0
        reasons.append(f"Volume dry-up unassessed: insufficient up/down observations ({len(up_vols)} up, {len(down_vols)} down, min {MIN_OBSERVATIONS} each)")

    # Weighted Sum Calculation Normalized by Active Weights
    total_score = 0.0
    weight_sum = sum(active_weights.values())
    for factor_key, factor_val in factor_scores.items():
        w = active_weights.get(factor_key, 0.0)
        total_score += factor_val * w

    norm_score = (total_score / weight_sum * 100.0) if weight_sum > 0 else 0.0
    norm_score = max(0.0, min(100.0, norm_score))

    # 7. Signal Classification with Truthful Reasons
    rng = range_high - range_low
    pos_in_range = ((latest_close - range_low) / rng) if rng > 1e-9 else 0.5

    classification = "NONE"
    if (
        norm_score >= 70.0
        and pos_in_range >= 0.80
        and bandwidth_pct <= 18.0
        and has_higher_lows
        and eff_lookback >= 20
        and factor_scores["higher_lows"] >= 0.5
    ):
        classification = "PRE_BREAKOUT"
        reasons.append(f"Pre-breakout tension: price at {pos_in_range * 100:.1f}% of range near resistance")
    elif norm_score >= 50.0 and cvd_result.absorption_detected and bandwidth_pct <= 22.0:
        classification = "EARLY_ACCUMULATION"
        reasons.append("Early accumulation: confirmed selling pressure absorption and base structure")
    elif (
        norm_score >= 50.0
        and has_higher_lows
        and bandwidth_pct <= 20.0
        and eff_lookback >= 20
        and factor_scores["higher_lows"] >= 0.5
    ):
        classification = "EARLY_ACCUMULATION"
        reasons.append("Early accumulation: ascending support base structure within consolidation")
    elif norm_score >= 35.0 and bandwidth_pct <= 22.0:
        classification = "BASE"
        reasons.append("Consolidation base: sideways trading without directional commitment")
    else:
        classification = "NONE"

    return AccumulationResult(
        accumulation_score=norm_score,
        classification=classification,
        range_high=range_high,
        range_low=range_low,
        bandwidth_pct=bandwidth_pct,
        higher_lows=has_higher_lows,
        atr_compression_ratio=atr_compression,
        volume_dryup=volume_dryup,
        dir_1pct=dir_1pct,
        dir_2pct=dir_2pct,
        orderbook_status=ob_status,
        factor_scores=factor_scores,
        factor_availability=factor_availability,
        data_confidence=cvd_result.data_confidence,
        reasons=reasons,
    )
