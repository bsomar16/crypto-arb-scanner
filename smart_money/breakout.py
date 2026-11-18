"""Smart Money Phase 2 — Breakout & Retest Engine for Binance Spot.

Authoritative Engineering Baseline: SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md
Subsystem: smart_money/
Operating Mode: Analytical Shadow Mode (Binance Spot Only, Long/BUY Only).
Zero Live Trading, Zero External Dispatch.

Implements Stage 1:
- Causal swing-high resistance pivot detection & complete-linkage clustering (§ 2)
- Pre-breakout readiness score S_ready (§ 3.2)
- Directional daily BTC regime classification via SMA200 & Wilder's ADX14 (§ 3.3, § 13.2)
- Mandatory breakout eligibility gates G1-G7 (§ 4.1)
- Breakout conviction scoring S_break (§ 4.2)
- Bar N+1 continuation decision tree hierarchy (§ 7)
- Multi-candle retest lifecycle & deterministic bounce logic (§ 8)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .volume_cvd import _atr_series, _extract_candle


# ---------------------------------------------------------------------------
# Section 2: Causal Swing-High Resistance Detection & Complete-Linkage Clustering
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pivot:
    """Immutable swing-high pivot candidate (§ 2.1)."""
    index: int          # Integer bar index j
    timestamp: int      # Kline open timestamp (ms)
    price: float        # Raw candle high price High_j
    volume: float       # Candle total base volume V_j

    @property
    def sort_key(self) -> Tuple[float, float, float, int]:
        """Canonical 4-level sorting key (§ 2.2): (-price, -volume, -timestamp, index)."""
        return (-self.price, -self.volume, -self.timestamp, self.index)


@dataclass
class ResistanceCluster:
    """Resistance cluster formed by complete-linkage compatibility (§ 2.4)."""
    members: List[Pivot] = field(default_factory=list)

    @property
    def r_level(self) -> float:
        """Arithmetic mean of all member pivot high prices."""
        if not self.members:
            return 0.0
        return sum(p.price for p in self.members) / len(self.members)

    @property
    def min_price(self) -> float:
        return min(p.price for p in self.members) if self.members else 0.0

    @property
    def max_price(self) -> float:
        return max(p.price for p in self.members) if self.members else 0.0

    @property
    def span(self) -> float:
        return self.max_price - self.min_price

    @property
    def touch_count(self) -> int:
        return len(self.members)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "r_level": round(self.r_level, 4),
            "touch_count": self.touch_count,
            "span": round(self.span, 4),
            "member_indices": [p.index for p in self.members],
            "member_prices": [round(p.price, 4) for p in self.members],
        }


def extract_resistance_pivots(
    candles: Sequence[Any],
    candidate_bar_index: int,
    lookback: int = 100,
    as_of_ms: Optional[int] = None,
) -> List[Pivot]:
    """Causal swing-high extraction strictly prior to candidate breakout bar N (§ 2.1).

    Lookback pool: j in [max(1, N - lookback), N - 2].
    A 3-bar local swing high requires High_j > High_{j-1} and High_j > High_{j+1}.
    Requires actual adjacent original candles (j-1, j, j+1).
    Never bridges missing, corrupt, or forming candles (§ Correction A).
    Preserves original candle indices and causal confirmation strictly prior to N.
    """
    n_candles = len(candles)
    if candidate_bar_index <= 0 or candidate_bar_index > n_candles:
        return []

    min_j = max(1, candidate_bar_index - lookback)
    max_j = candidate_bar_index - 2  # j + 1 <= candidate_bar_index - 1
    if max_j < min_j:
        return []

    pivots: List[Pivot] = []
    for j in range(min_j, max_j + 1):
        if j - 1 < 0 or j + 1 >= candidate_bar_index or j + 1 >= n_candles:
            continue

        prev_c, rej_prev = _extract_candle(candles[j - 1], as_of_ms=as_of_ms)
        curr_c, rej_curr = _extract_candle(candles[j], as_of_ms=as_of_ms)
        next_c, rej_next = _extract_candle(candles[j + 1], as_of_ms=as_of_ms)

        # Do not bridge missing, corrupt, or forming candles
        if prev_c is None or curr_c is None or next_c is None:
            continue

        # Timestamps must be strictly monotonic
        t_prev, ct_prev, _, h_prev, _, _, _, _, _ = prev_c
        t_curr, ct_curr, _, h_curr, _, _, v_curr, _, _ = curr_c
        t_next, ct_next, _, h_next, _, _, _, _, _ = next_c

        if not (t_prev < t_curr < t_next):
            continue
        if ct_prev > 0 and ct_curr > 0 and ct_next > 0:
            if not (ct_prev < ct_curr < ct_next):
                continue

        if h_curr > h_prev and h_curr > h_next:
            pivots.append(
                Pivot(
                    index=j,
                    timestamp=t_curr,
                    price=h_curr,
                    volume=v_curr,
                )
            )

    return pivots


def is_chronologically_compatible(
    p_a: Pivot,
    p_b: Pivot,
    atr_ref: float,
    candles: Sequence[Any],
    as_of_ms: Optional[int] = None,
) -> bool:
    """Check pairwise structural compatibility between two pivots p_a, p_b (§ 2.3).

    Guarantees:
    1. Tolerance Band: |P_a - P_b| <= 0.35 * ATR_ref
    2. Touch Separation: |i_b - i_a| >= 4 (>= 3 intervening closed candles)
    3. Intervening Pullback: min_{k in [i_a + 1, i_b - 1]} Low_k <= min(P_a, P_b) - 0.50 * ATR_ref
    Every intervening candle must exist and be valid/closed; no silent skipping (§ Correction B).
    """
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        return False
    if not isinstance(p_a, Pivot) or not isinstance(p_b, Pivot):
        return False
    if isinstance(p_a.index, bool) or not isinstance(p_a.index, int) or p_a.index < 0:
        return False
    if isinstance(p_b.index, bool) or not isinstance(p_b.index, int) or p_b.index < 0:
        return False
    if not math.isfinite(p_a.price) or p_a.price <= 0.0 or not math.isfinite(p_b.price) or p_b.price <= 0.0:
        return False

    if p_a.index == p_b.index:
        return p_a == p_b

    # Order chronologically
    first, second = (p_a, p_b) if p_a.index < p_b.index else (p_b, p_a)
    if first.timestamp >= second.timestamp:
        return False

    # 1. Tolerance Band
    if abs(first.price - second.price) > 0.35 * atr_ref + 1e-9:
        return False

    # 2. Touch Separation
    if (second.index - first.index) < 4:
        return False

    # 3. Intervening Pullback
    # Verify every intervening candle k in [first.index + 1, second.index - 1]
    intervening_lows = []
    last_t = None
    for k in range(first.index + 1, second.index):
        if k >= len(candles):
            return False  # Missing intervening candle / gap
        raw = candles[k]
        c_tuple, _ = _extract_candle(raw, as_of_ms=as_of_ms)
        if c_tuple is None:
            return False  # Corrupt, missing, or forming intervening candle
        t_k = c_tuple[0]
        if last_t is not None and t_k <= last_t:
            return False  # Non-monotonic intervening timestamp
        last_t = t_k
        intervening_lows.append(c_tuple[4])  # Low is index 4

    if not intervening_lows:
        return False

    min_intervening_low = min(intervening_lows)
    required_pullback_level = min(first.price, second.price) - 0.50 * atr_ref
    if min_intervening_low > required_pullback_level + 1e-9:
        return False

    return True


def cluster_resistance_pivots(
    pivots: List[Pivot],
    atr_ref: float,
    c_prev: float,
    candles: Sequence[Any],
    as_of_ms: Optional[int] = None,
) -> List[ResistanceCluster]:
    """Deterministic complete-linkage clustering algorithm (§ 2.4).

    Sorts candidate pivots canonically, greedily groups into clusters where
    every member pair satisfies structural compatibility and total cluster span <= 0.35 * ATR_ref.
    Filters valid clusters: >= 2 touches and strictly overhead (R_level > c_prev).
    """
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        return []
    if isinstance(c_prev, bool) or not isinstance(c_prev, (int, float)) or not math.isfinite(c_prev) or c_prev <= 0.0:
        return []
    if not isinstance(pivots, (list, tuple)) or not pivots:
        return []
    if any(not isinstance(p, Pivot) for p in pivots):
        return []

    sorted_pivots = sorted(pivots, key=lambda p: p.sort_key)
    clusters: List[ResistanceCluster] = []

    for pivot in sorted_pivots:
        assigned = False
        for cluster in clusters:
            # Complete compatibility check: pivot must be compatible with EVERY member
            if all(
                is_chronologically_compatible(p, pivot, atr_ref, candles, as_of_ms=as_of_ms)
                for p in cluster.members
            ):
                # Ensure cluster diameter remains within tolerance
                all_prices = [p.price for p in cluster.members] + [pivot.price]
                if (max(all_prices) - min(all_prices)) <= 0.35 * atr_ref + 1e-9:
                    cluster.members.append(pivot)
                    assigned = True
                    break
        if not assigned:
            clusters.append(ResistanceCluster(members=[pivot]))

    # Filter valid clusters: at least 2 touches and strictly overhead
    valid_clusters = []
    for c in clusters:
        if len(c.members) >= 2 and c.r_level > c_prev + 1e-9:
            valid_clusters.append(c)

    return valid_clusters


def select_primary_resistance_cluster(
    valid_clusters: List[ResistanceCluster],
    c_prev: float,
) -> Optional[ResistanceCluster]:
    """Select primary resistance cluster overhead (§ 2.4).

    Primary sort: Nearest overhead resistance level (r_level - c_prev).
    Tie-breakers: Highest touch count descending, lowest span ascending,
    and final deterministic tie-breaker derived from canonical original Pivot identities (§ Correction D).
    """
    if not valid_clusters:
        return None

    def _cluster_selection_key(c: ResistanceCluster) -> Tuple[Any, ...]:
        member_keys = tuple(p.sort_key for p in sorted(c.members, key=lambda p: p.sort_key))
        return (c.r_level - c_prev, -c.touch_count, c.span, member_keys)

    return min(valid_clusters, key=_cluster_selection_key)


# ---------------------------------------------------------------------------
# Section 3: Pre-Breakout Readiness & Directional Market Regime
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreBreakoutReadiness:
    """Pre-breakout readiness evaluation result (§ 3.2)."""
    s_ready: float
    s_accum: float
    s_prox: float
    s_comp: float
    s_dir: float
    bbw: float
    dir_1pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "s_ready": round(self.s_ready, 2),
            "s_accum": round(self.s_accum, 2),
            "s_prox": round(self.s_prox, 2),
            "s_comp": round(self.s_comp, 2),
            "s_dir": round(self.s_dir, 2),
            "bbw": round(self.bbw, 4),
            "dir_1pct": round(self.dir_1pct, 4),
        }


def clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    """Deterministic numeric clamp to [low, high]."""
    return max(low, min(high, val))


def evaluate_pre_breakout_readiness(
    s_accum: float,
    r_level: float,
    c_prev: float,
    atr_ref: float,
    bbw_prev: float,
    dir_1pct: Optional[float] = None,
) -> PreBreakoutReadiness:
    """Calculate Pre-Breakout Readiness Score S_ready on closed bar N-1 (§ 3.2).

    S_ready = 0.35 * S_accum + 0.25 * S_prox + 0.20 * S_comp + 0.20 * S_dir in [0.0, 100.0]
    - S_prox = max(0.0, 1.0 - (R_level - C_{N-1}) / (1.5 * ATR_ref)) * 100.0
    - S_comp = max(0.0, 1.0 - BBW_{N-1} / 0.08) * 100.0
    - S_dir  = clamp((DIR_1pct - 0.50) / 0.20, 0.0, 1.0) * 100.0 (default 50.0 if unassessed)
    """
    # Strict numeric input validation (§ Correction C)
    if isinstance(s_accum, bool) or not isinstance(s_accum, (int, float)) or not math.isfinite(s_accum) or s_accum < 0.0 or s_accum > 100.0:
        raise ValueError(f"s_accum must be finite number in [0.0, 100.0], got {s_accum}")
    if isinstance(r_level, bool) or not isinstance(r_level, (int, float)) or not math.isfinite(r_level) or r_level <= 0.0:
        raise ValueError(f"r_level must be finite positive number, got {r_level}")
    if isinstance(c_prev, bool) or not isinstance(c_prev, (int, float)) or not math.isfinite(c_prev) or c_prev <= 0.0:
        raise ValueError(f"c_prev must be finite positive number, got {c_prev}")
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        raise ValueError(f"atr_ref must be finite positive number, got {atr_ref}")
    if isinstance(bbw_prev, bool) or not isinstance(bbw_prev, (int, float)) or not math.isfinite(bbw_prev) or bbw_prev < 0.0:
        raise ValueError(f"bbw_prev must be finite non-negative number, got {bbw_prev}")
    if dir_1pct is not None:
        if isinstance(dir_1pct, bool) or not isinstance(dir_1pct, (int, float)) or not math.isfinite(dir_1pct) or dir_1pct < 0.0 or dir_1pct > 1.0:
            raise ValueError(f"dir_1pct must be finite number in [0.0, 1.0] or None, got {dir_1pct}")

    # 1. Proximity Score
    if atr_ref > 1e-9:
        dist = max(0.0, r_level - c_prev)
        s_prox = max(0.0, 1.0 - (dist / (1.5 * atr_ref))) * 100.0
    else:
        s_prox = 0.0

    # 2. Compression Score (BBW_prev / 0.08)
    s_comp = max(0.0, 1.0 - (bbw_prev / 0.08)) * 100.0

    # 3. Directional Orderbook Depth Score
    if dir_1pct is not None and not math.isnan(dir_1pct):
        s_dir = clamp((dir_1pct - 0.50) / 0.20, 0.0, 1.0) * 100.0
        eff_dir = dir_1pct
    else:
        s_dir = 50.0  # Specification default when unassessed
        eff_dir = 0.50

    # Composite Score
    s_ready = (
        0.35 * s_accum
        + 0.25 * s_prox
        + 0.20 * s_comp
        + 0.20 * s_dir
    )
    s_ready = clamp(s_ready, 0.0, 100.0)

    return PreBreakoutReadiness(
        s_ready=s_ready,
        s_accum=s_accum,
        s_prox=s_prox,
        s_comp=s_comp,
        s_dir=s_dir,
        bbw=bbw_prev,
        dir_1pct=eff_dir,
    )


# ---------------------------------------------------------------------------
# Section 3.3 & Section 13.2: Directional Daily Market Regime via Wilder's ADX14
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MacroRegimeResult:
    """Directional daily BTC market regime classification result (§ 3.3)."""
    regime: str             # "WARMUP", "BEAR", "SIDEWAYS", "BULL"
    sma200: Optional[float]
    adx14: Optional[float]
    plus_di: Optional[float]
    minus_di: Optional[float]
    btc_close: float
    bars_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "sma200": round(self.sma200, 2) if self.sma200 is not None else None,
            "adx14": round(self.adx14, 2) if self.adx14 is not None else None,
            "plus_di": round(self.plus_di, 2) if self.plus_di is not None else None,
            "minus_di": round(self.minus_di, 2) if self.minus_di is not None else None,
            "btc_close": round(self.btc_close, 2),
            "bars_count": self.bars_count,
        }


def calculate_wilder_adx14(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate Wilder's ADX14, +DI14, -DI14 strictly per Section 13.2 (§ Correction F).

    Returns:
        (adx14, plus_di14, minus_di14)
    """
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        return None, None, None

    if not isinstance(highs, Sequence) or not isinstance(lows, Sequence) or not isinstance(closes, Sequence):
        return None, None, None

    n = len(closes)
    # Requires at least 2 * period bars for initial smoothed ADX
    if n < (2 * period) or len(highs) < n or len(lows) < n:
        return None, None, None

    # Validate numerical integrity of input sequences
    for i in range(n):
        h, l, c = highs[i], lows[i], closes[i]
        if isinstance(h, bool) or not isinstance(h, (int, float)) or not math.isfinite(h) or h <= 0.0:
            return None, None, None
        if isinstance(l, bool) or not isinstance(l, (int, float)) or not math.isfinite(l) or l <= 0.0:
            return None, None, None
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or c <= 0.0:
            return None, None, None
        if h < l:
            return None, None, None

    # Step 1: True Range and Directional Movement
    tr_list: List[float] = []
    plus_dm_list: List[float] = []
    minus_dm_list: List[float] = []

    for t in range(1, n):
        h, l, prev_c = highs[t], lows[t], closes[t - 1]
        prev_h, prev_l = highs[t - 1], lows[t - 1]

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        up_move = h - prev_h
        down_move = prev_l - l

        plus_dm = up_move if (up_move > down_move and up_move > 0.0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0.0) else 0.0

        tr_list.append(tr)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    # Step 2: Initial 14-period Wilder smoothing (arithmetic sum / period)
    atr = sum(tr_list[:period]) / period
    smooth_plus_dm = sum(plus_dm_list[:period]) / period
    smooth_minus_dm = sum(minus_dm_list[:period]) / period

    dx_list: List[float] = []

    plus_di = (smooth_plus_dm / atr * 100.0) if atr > 1e-12 else 0.0
    minus_di = (smooth_minus_dm / atr * 100.0) if atr > 1e-12 else 0.0
    sum_di = plus_di + minus_di
    dx = (abs(plus_di - minus_di) / sum_di * 100.0) if sum_di > 1e-12 else 0.0
    dx_list.append(dx)

    # Step 3: Running Wilder smoothing
    for t in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[t]) / period
        smooth_plus_dm = (smooth_plus_dm * (period - 1) + plus_dm_list[t]) / period
        smooth_minus_dm = (smooth_minus_dm * (period - 1) + minus_dm_list[t]) / period

        plus_di = (smooth_plus_dm / atr * 100.0) if atr > 1e-12 else 0.0
        minus_di = (smooth_minus_dm / atr * 100.0) if atr > 1e-12 else 0.0
        sum_di = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / sum_di * 100.0) if sum_di > 1e-12 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None, plus_di, minus_di

    # Step 4: First ADX is the average of first 14 DX values
    adx = sum(dx_list[:period]) / period

    # Step 5: Subsequent ADX smoothing
    for dx_val in dx_list[period:]:
        adx = (adx * (period - 1) + dx_val) / period

    return adx, plus_di, minus_di


def classify_macro_regime(
    daily_btc_candles: Sequence[Any],
    as_of_ms: Optional[int] = None,
) -> MacroRegimeResult:
    """Directional Market Regime Hierarchy on closed Daily BTC bars (§ 3.3).

    Stage 1 — Data Adequacy: Requires >= 200 closed daily BTC bars with strictly monotonic
             timestamps and zero gaps/corruption, else WARMUP (§ Correction E).
    Stage 2 — Macro Trend vs SMA200: If P_{BTC, close} < SMA200(BTC) => BEAR.
    Stage 3 — Directional Trend Confirmation via Wilder's ADX14:
      If ADX14 is unavailable => WARMUP (cannot confirm trend without ADX, § Correction E).
      If ADX14 < 20.0 => SIDEWAYS
      If ADX14 >= 20.0:
        If +DI14 > -DI14 => BULL
        If +DI14 <= -DI14 => SIDEWAYS (prevents classifying strong downtrend as Bullish)
    """
    if not isinstance(daily_btc_candles, Sequence):
        return MacroRegimeResult(
            regime="WARMUP",
            sma200=None,
            adx14=None,
            plus_di=None,
            minus_di=None,
            btc_close=0.0,
            bars_count=0,
        )

    parsed = []
    last_open_t = -1
    for raw in daily_btc_candles:
        c_tuple, rej = _extract_candle(raw, as_of_ms=as_of_ms)
        if c_tuple is None:
            # Corrupt, missing, or forming candle in daily sequence
            return MacroRegimeResult(
                regime="WARMUP",
                sma200=None,
                adx14=None,
                plus_di=None,
                minus_di=None,
                btc_close=parsed[-1][5] if parsed else 0.0,
                bars_count=len(parsed),
            )
        # Verify strict timestamp monotonicity (no duplicate timestamps or out-of-order bars)
        if c_tuple[0] <= last_open_t:
            return MacroRegimeResult(
                regime="WARMUP",
                sma200=None,
                adx14=None,
                plus_di=None,
                minus_di=None,
                btc_close=c_tuple[5],
                bars_count=len(parsed),
            )
        last_open_t = c_tuple[0]
        parsed.append(c_tuple)

    n_bars = len(parsed)
    if n_bars < 200:
        latest_c = parsed[-1][5] if parsed else 0.0
        return MacroRegimeResult(
            regime="WARMUP",
            sma200=None,
            adx14=None,
            plus_di=None,
            minus_di=None,
            btc_close=latest_c,
            bars_count=n_bars,
        )

    closes = [p[5] for p in parsed]
    highs = [p[3] for p in parsed]
    lows = [p[4] for p in parsed]

    latest_close = closes[-1]
    sma200 = sum(closes[-200:]) / 200.0

    if latest_close < sma200:
        return MacroRegimeResult(
            regime="BEAR",
            sma200=sma200,
            adx14=None,
            plus_di=None,
            minus_di=None,
            btc_close=latest_close,
            bars_count=n_bars,
        )

    adx14, plus_di, minus_di = calculate_wilder_adx14(highs, lows, closes, period=14)
    if adx14 is None:
        # Data inadequate to compute ADX14: return WARMUP (do not reinterpret as SIDEWAYS)
        return MacroRegimeResult(
            regime="WARMUP",
            sma200=sma200,
            adx14=None,
            plus_di=plus_di,
            minus_di=minus_di,
            btc_close=latest_close,
            bars_count=n_bars,
        )

    if adx14 < 20.0:
        regime = "SIDEWAYS"
    else:
        if plus_di is not None and minus_di is not None and plus_di > minus_di:
            regime = "BULL"
        else:
            regime = "SIDEWAYS"

    return MacroRegimeResult(
        regime=regime,
        sma200=sma200,
        adx14=adx14,
        plus_di=plus_di,
        minus_di=minus_di,
        btc_close=latest_close,
        bars_count=n_bars,
    )


# ---------------------------------------------------------------------------
# Section 4: Breakout Eligibility Gates & Conviction Scoring Model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BreakoutGatesResult:
    """Breakout eligibility gates G1-G7 evaluation result (§ 4.1)."""
    passed: bool
    g1_clearance: bool
    g2_volume: bool
    g3_taker: bool
    g4_close: bool
    g5_prior_state: bool
    g6_regime: bool
    g7_data_confidence: bool
    failed_gates: List[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_gates": list(self.failed_gates),
            "details": dict(self.details),
        }


def evaluate_breakout_gates(
    candle_n: Any,
    r_level: float,
    atr_ref: float,
    rvol_n: float,
    z_vol_n: float,
    delta_norm_n: float,
    tbr_n: float,
    prior_state: str,
    s_accum_prev: float,
    daily_btc_regime: str,
    data_confidence: float,
    as_of_ms: Optional[int] = None,
) -> BreakoutGatesResult:
    """Evaluate mandatory Breakout Eligibility Gates G1-G7 (§ 4.1).

    All 7 gates must pass simultaneously on closed breakout bar N:
    - G1 (Clearance): C_N > R_level + 0.15 * ATR_ref
    - G2 (Volume Expansion): RVOL_N >= 1.75 or Z_vol,N >= 2.0
    - G3 (Taker Buying Dominance): Delta_norm,N >= +0.15 and TBR_N >= 0.55
    - G4 (Decisive Close): C_N > O_N and (C_N - Low_N) / (High_N - Low_N) >= 0.70
    - G5 (Prior State Qualification): prior state was PRE_BREAKOUT or (ACCUMULATION with S_accum >= 65.0)
    - G6 (Regime Alignment): daily BTC regime in {"BULL", "SIDEWAYS"} (BEAR and WARMUP strictly rejected)
    - G7 (Data Confidence): data confidence score >= 70.0
    """
    # Strict numeric input validation (§ Correction C)
    if isinstance(r_level, bool) or not isinstance(r_level, (int, float)) or not math.isfinite(r_level) or r_level <= 0.0:
        raise ValueError(f"r_level must be finite positive number, got {r_level}")
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        raise ValueError(f"atr_ref must be finite positive number, got {atr_ref}")
    if isinstance(rvol_n, bool) or not isinstance(rvol_n, (int, float)) or not math.isfinite(rvol_n) or rvol_n < 0.0:
        raise ValueError(f"rvol_n must be finite non-negative number, got {rvol_n}")
    if isinstance(z_vol_n, bool) or not isinstance(z_vol_n, (int, float)) or not math.isfinite(z_vol_n):
        raise ValueError(f"z_vol_n must be finite number, got {z_vol_n}")
    if isinstance(delta_norm_n, bool) or not isinstance(delta_norm_n, (int, float)) or not math.isfinite(delta_norm_n) or delta_norm_n < -1.0 or delta_norm_n > 1.0:
        raise ValueError(f"delta_norm_n must be finite number in [-1.0, 1.0], got {delta_norm_n}")
    if isinstance(tbr_n, bool) or not isinstance(tbr_n, (int, float)) or not math.isfinite(tbr_n) or tbr_n < 0.0 or tbr_n > 1.0:
        raise ValueError(f"tbr_n must be finite number in [0.0, 1.0], got {tbr_n}")
    if isinstance(s_accum_prev, bool) or not isinstance(s_accum_prev, (int, float)) or not math.isfinite(s_accum_prev) or s_accum_prev < 0.0 or s_accum_prev > 100.0:
        raise ValueError(f"s_accum_prev must be finite number in [0.0, 100.0], got {s_accum_prev}")
    if isinstance(data_confidence, bool) or not isinstance(data_confidence, (int, float)) or not math.isfinite(data_confidence) or data_confidence < 0.0 or data_confidence > 100.0:
        raise ValueError(f"data_confidence must be finite number in [0.0, 100.0], got {data_confidence}")
    if not isinstance(prior_state, str):
        raise ValueError(f"prior_state must be str, got {type(prior_state).__name__}")
    if not isinstance(daily_btc_regime, str):
        raise ValueError(f"daily_btc_regime must be str, got {type(daily_btc_regime).__name__}")

    c_tuple, rej = _extract_candle(candle_n, as_of_ms=as_of_ms)
    if c_tuple is None:
        return BreakoutGatesResult(
            passed=False,
            g1_clearance=False,
            g2_volume=False,
            g3_taker=False,
            g4_close=False,
            g5_prior_state=False,
            g6_regime=False,
            g7_data_confidence=False,
            failed_gates=["DATA_CORRUPT"],
            details={"error": rej},
        )

    _, _, o_n, h_n, l_n, c_n, v_n, v_tb_n, _ = c_tuple
    failed_gates = []

    # G1: Clearance
    required_clearance = r_level + 0.15 * atr_ref
    g1 = (c_n > required_clearance + 1e-9)
    if not g1:
        failed_gates.append("G1_CLEARANCE")

    # G2: Volume Expansion
    g2 = (rvol_n >= 1.75 - 1e-9) or (z_vol_n >= 2.0 - 1e-9)
    if not g2:
        failed_gates.append("G2_VOLUME")

    # G3: Taker Buying Dominance
    g3 = (delta_norm_n >= 0.15 - 1e-9) and (tbr_n >= 0.55 - 1e-9)
    if not g3:
        failed_gates.append("G3_TAKER_DOMINANCE")

    # G4: Decisive Close
    bar_range = h_n - l_n
    close_loc = (c_n - l_n) / bar_range if bar_range > 1e-9 else 0.0
    g4 = (c_n > o_n + 1e-9) and (close_loc >= 0.70 - 1e-9)
    if not g4:
        failed_gates.append("G4_DECISIVE_CLOSE")

    # G5: Prior State Qualification
    g5 = (prior_state == "PRE_BREAKOUT") or (prior_state == "ACCUMULATION" and s_accum_prev >= 65.0 - 1e-9)
    if not g5:
        failed_gates.append("G5_PRIOR_STATE")

    # G6: Regime Alignment
    g6 = daily_btc_regime in ("BULL", "SIDEWAYS")
    if not g6:
        failed_gates.append("G6_REGIME")

    # G7: Data Confidence
    g7 = (data_confidence >= 70.0 - 1e-9)
    if not g7:
        failed_gates.append("G7_DATA_CONFIDENCE")

    passed = (len(failed_gates) == 0)

    details = {
        "c_n": c_n,
        "required_clearance": round(required_clearance, 4),
        "rvol_n": round(rvol_n, 2),
        "z_vol_n": round(z_vol_n, 2),
        "delta_norm_n": round(delta_norm_n, 4),
        "tbr_n": round(tbr_n, 4),
        "close_location": round(close_loc, 4),
        "prior_state": prior_state,
        "s_accum_prev": round(s_accum_prev, 2),
        "regime": daily_btc_regime,
        "data_confidence": round(data_confidence, 1),
    }

    return BreakoutGatesResult(
        passed=passed,
        g1_clearance=g1,
        g2_volume=g2,
        g3_taker=g3,
        g4_close=g4,
        g5_prior_state=g5,
        g6_regime=g6,
        g7_data_confidence=g7,
        failed_gates=failed_gates,
        details=details,
    )


@dataclass(frozen=True)
class BreakoutScoreResult:
    """Breakout conviction score S_break result (§ 4.2)."""
    s_break: float
    s_clear: float
    s_vol: float
    s_delta: float
    s_shape: float
    s_context: float
    is_eligible: bool
    continuation_eligible: bool
    retest_mandatory: bool
    factors: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "s_break": round(self.s_break, 2),
            "s_clear": round(self.s_clear, 2),
            "s_vol": round(self.s_vol, 2),
            "s_delta": round(self.s_delta, 2),
            "s_shape": round(self.s_shape, 2),
            "s_context": round(self.s_context, 2),
            "is_eligible": self.is_eligible,
            "continuation_eligible": self.continuation_eligible,
            "retest_mandatory": self.retest_mandatory,
        }


def calculate_breakout_score(
    candle_n: Any,
    r_level: float,
    atr_ref: float,
    rvol_n: float,
    z_vol_n: float,
    delta_norm_n: float,
    s_context: float,
    regime: str,
    as_of_ms: Optional[int] = None,
) -> BreakoutScoreResult:
    """Compute Breakout Conviction Score S_break (§ 4.2).

    S_break = 0.25 * S_clear + 0.25 * S_vol + 0.20 * S_delta + 0.15 * S_shape + 0.15 * S_context in [0.0, 100.0]
    - S_clear = 100 * clamp((C_N - (R_level + 0.10 * ATR_ref)) / (0.30 * ATR_ref), 0.0, 1.0)
    - S_vol   = 100 * max(clamp((RVOL_N - 1.75) / 1.75, 0.0, 1.0), clamp((Z_vol,N - 2.00) / 2.00, 0.0, 1.0))
    - S_delta = 100 * clamp((Delta_norm,N - 0.15) / 0.30, 0.0, 1.0)
    - S_shape = 100 * [0.50 * clamp(((C_N - O_N) / (H_N - L_N) - 0.50) / 0.35, 0.0, 1.0) +
                       0.50 * clamp(((C_N - L_N) / (H_N - L_N) - 0.65) / 0.25, 0.0, 1.0)]
    - S_context = S_ready (or S_accum if entering directly from ACCUMULATION)

    Thresholds:
    - Setup requires S_break >= 65.0 for eligibility.
    - If S_break >= 80.0 and Regime == BULL => Direct continuation eligible.
    - If 65.0 <= S_break < 80.0 or Regime == SIDEWAYS => Support retest mandatory.
    """
    # Strict numeric input validation (§ Correction C)
    if isinstance(r_level, bool) or not isinstance(r_level, (int, float)) or not math.isfinite(r_level) or r_level <= 0.0:
        raise ValueError(f"r_level must be finite positive number, got {r_level}")
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        raise ValueError(f"atr_ref must be finite positive number, got {atr_ref}")
    if isinstance(rvol_n, bool) or not isinstance(rvol_n, (int, float)) or not math.isfinite(rvol_n) or rvol_n < 0.0:
        raise ValueError(f"rvol_n must be finite non-negative number, got {rvol_n}")
    if isinstance(z_vol_n, bool) or not isinstance(z_vol_n, (int, float)) or not math.isfinite(z_vol_n):
        raise ValueError(f"z_vol_n must be finite number, got {z_vol_n}")
    if isinstance(delta_norm_n, bool) or not isinstance(delta_norm_n, (int, float)) or not math.isfinite(delta_norm_n) or delta_norm_n < -1.0 or delta_norm_n > 1.0:
        raise ValueError(f"delta_norm_n must be finite number in [-1.0, 1.0], got {delta_norm_n}")
    if isinstance(s_context, bool) or not isinstance(s_context, (int, float)) or not math.isfinite(s_context) or s_context < 0.0 or s_context > 100.0:
        raise ValueError(f"s_context must be finite number in [0.0, 100.0], got {s_context}")
    if not isinstance(regime, str) or regime not in ("BULL", "BEAR", "SIDEWAYS", "WARMUP"):
        raise ValueError(f"regime must be one of ('BULL', 'BEAR', 'SIDEWAYS', 'WARMUP'), got {regime}")

    c_tuple, _ = _extract_candle(candle_n, as_of_ms=as_of_ms)
    if c_tuple is None or atr_ref <= 1e-9:
        return BreakoutScoreResult(
            s_break=0.0,
            s_clear=0.0,
            s_vol=0.0,
            s_delta=0.0,
            s_shape=0.0,
            s_context=0.0,
            is_eligible=False,
            continuation_eligible=False,
            retest_mandatory=False,
            factors={},
        )

    _, _, o_n, h_n, l_n, c_n, _, _, _ = c_tuple

    # 1. Clearance Score
    clearance_dist = c_n - (r_level + 0.10 * atr_ref)
    g1 = clamp(clearance_dist / (0.30 * atr_ref), 0.0, 1.0)
    s_clear = 100.0 * g1

    # 2. Volume Expansion Score
    rvol_term = clamp((rvol_n - 1.75) / 1.75, 0.0, 1.0)
    zvol_term = clamp((z_vol_n - 2.00) / 2.00, 0.0, 1.0)
    g2 = max(rvol_term, zvol_term)
    s_vol = 100.0 * g2

    # 3. Taker Flow Score
    g3 = clamp((delta_norm_n - 0.15) / 0.30, 0.0, 1.0)
    s_delta = 100.0 * g3

    # 4. Candle Shape Decisiveness
    bar_range = h_n - l_n
    if bar_range > 1e-8:
        body_ratio = (c_n - o_n) / bar_range
        close_ratio = (c_n - l_n) / bar_range
        term_body = clamp((body_ratio - 0.50) / 0.35, 0.0, 1.0)
        term_close = clamp((close_ratio - 0.65) / 0.25, 0.0, 1.0)
        g4 = 0.50 * term_body + 0.50 * term_close
    else:
        g4 = 0.0
    s_shape = 100.0 * g4

    # 5. Composite Conviction Score
    s_break = (
        0.25 * s_clear
        + 0.25 * s_vol
        + 0.20 * s_delta
        + 0.15 * s_shape
        + 0.15 * s_context
    )
    s_break = clamp(s_break, 0.0, 100.0)

    is_eligible = (s_break >= 65.0 - 1e-9)
    continuation_eligible = is_eligible and (s_break >= 80.0 - 1e-9) and (regime == "BULL")
    retest_mandatory = is_eligible and (s_break < 80.0 - 1e-9 or regime == "SIDEWAYS")

    return BreakoutScoreResult(
        s_break=s_break,
        s_clear=s_clear,
        s_vol=s_vol,
        s_delta=s_delta,
        s_shape=s_shape,
        s_context=s_context,
        is_eligible=is_eligible,
        continuation_eligible=continuation_eligible,
        retest_mandatory=retest_mandatory,
        factors={
            "s_clear": s_clear,
            "s_vol": s_vol,
            "s_delta": s_delta,
            "s_shape": s_shape,
            "s_context": s_context,
        },
    )


# ---------------------------------------------------------------------------
# Section 7: Exhaustive N+1 Continuation Decision Tree
# ---------------------------------------------------------------------------

def evaluate_n1_continuation(
    candle_n: Any,
    candle_n1: Any,
    r_level: float,
    atr_ref: float,
    vol_baseline: float,  # V_bar_20(N-1)
    s_break: float,
    regime: str,
    delta_norm_n1: float,
    as_of_ms: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Evaluate Bar N+1 Continuation Decision Tree Hierarchy (§ 7).

    Priority 1: if not p_data_valid => DATA_INVALID -> NONE
    Priority 2: if p_floor_breach   => STRUCTURE_INVALIDATED -> NONE
    Priority 3: if p_wick_breach    => STRUCTURE_INVALIDATED -> NONE
    Priority 4: if S_break >= 80.0 and Regime == BULL and p_cont_price and p_cont_flow and not p_wick_breach
                => CONTINUATION_CONFIRMED
    Priority 5: if p_retest_touch and p_retest_close and not p_wick_breach and p_retest_vol
                => RETEST_PENDING
    Priority 6: if p_retest_touch and not p_retest_vol
                => STRUCTURE_INVALIDATED -> NONE
    Priority 7: Indecision fallback => STALLED_PENDING
    """
    # Priority 1: Validate numeric parameters and regimes (§ Correction C, F)
    if isinstance(r_level, bool) or not isinstance(r_level, (int, float)) or not math.isfinite(r_level) or r_level <= 0.0:
        return "DATA_INVALID", {"rejection_reason": f"Invalid r_level: {r_level}"}
    if isinstance(atr_ref, bool) or not isinstance(atr_ref, (int, float)) or not math.isfinite(atr_ref) or atr_ref <= 0.0:
        return "DATA_INVALID", {"rejection_reason": f"Invalid atr_ref: {atr_ref}"}
    if isinstance(vol_baseline, bool) or not isinstance(vol_baseline, (int, float)) or not math.isfinite(vol_baseline) or vol_baseline <= 0.0:
        return "DATA_INVALID", {"rejection_reason": f"Invalid vol_baseline: {vol_baseline}"}
    if isinstance(s_break, bool) or not isinstance(s_break, (int, float)) or not math.isfinite(s_break) or s_break < 0.0 or s_break > 100.0:
        return "DATA_INVALID", {"rejection_reason": f"Invalid s_break: {s_break}"}
    if isinstance(delta_norm_n1, bool) or not isinstance(delta_norm_n1, (int, float)) or not math.isfinite(delta_norm_n1) or delta_norm_n1 < -1.0 or delta_norm_n1 > 1.0:
        return "DATA_INVALID", {"rejection_reason": f"Invalid delta_norm_n1: {delta_norm_n1}"}
    if regime not in ("BULL", "BEAR", "SIDEWAYS", "WARMUP"):
        return "DATA_INVALID", {"rejection_reason": f"Unsupported regime: {regime}"}

    c_n, rej_n = _extract_candle(candle_n, as_of_ms=as_of_ms)
    c_n1, rej_n1 = _extract_candle(candle_n1, as_of_ms=as_of_ms)

    # Priority 1: Missing or corrupt feed
    if c_n is None or c_n1 is None:
        return "DATA_INVALID", {"rejection_reason": rej_n1 or rej_n}

    t_n, ct_n, o_n, h_n, l_n, c_n_val, v_n, _, _ = c_n
    t_n1, ct_n1, o_n1, h_n1, l_n1, c_n1_val, v_n1, _, _ = c_n1

    # Priority 1: Chronological validation
    if t_n1 <= t_n:
        return "DATA_INVALID", {"rejection_reason": f"Non-chronological candles: N open {t_n} >= N+1 open {t_n1}"}
    if ct_n > 0 and ct_n1 > 0 and ct_n1 <= ct_n:
        return "DATA_INVALID", {"rejection_reason": f"Non-chronological close times: N close {ct_n} >= N+1 close {ct_n1}"}

    floor_limit = r_level - 0.10 * atr_ref
    wick_limit = r_level - 0.25 * atr_ref
    mid_break = (o_n + c_n_val) / 2.0
    retest_zone_high = r_level + 0.20 * atr_ref

    p_floor_breach = (c_n1_val < floor_limit - 1e-9)
    p_wick_breach = (l_n1 < wick_limit - 1e-9)
    p_cont_price = (c_n1_val > c_n_val + 1e-9) and (l_n1 >= mid_break - 1e-9)
    p_cont_flow = (delta_norm_n1 > 0.0 + 1e-9)
    p_retest_touch = (l_n1 <= retest_zone_high + 1e-9)
    p_retest_vol = (v_n1 <= 1.05 * vol_baseline + 1e-9)
    p_retest_close = (c_n1_val >= floor_limit - 1e-9)

    predicates = {
        "p_floor_breach": p_floor_breach,
        "p_wick_breach": p_wick_breach,
        "p_cont_price": p_cont_price,
        "p_cont_flow": p_cont_flow,
        "p_retest_touch": p_retest_touch,
        "p_retest_vol": p_retest_vol,
        "p_retest_close": p_retest_close,
        "floor_limit": round(floor_limit, 4),
        "wick_limit": round(wick_limit, 4),
        "mid_break": round(mid_break, 4),
        "retest_zone_high": round(retest_zone_high, 4),
        "v_n1": v_n1,
        "vol_baseline": vol_baseline,
    }

    # Priority 2: Floor Breach (Close)
    if p_floor_breach:
        return "STRUCTURE_INVALIDATED", predicates

    # Priority 3: Wick Breach (Low)
    if p_wick_breach:
        return "STRUCTURE_INVALIDATED", predicates

    # Priority 4: Direct Continuation Confirmation
    if (
        s_break >= 80.0 - 1e-9
        and regime == "BULL"
        and p_cont_price
        and p_cont_flow
        and not p_wick_breach
    ):
        return "CONTINUATION_CONFIRMED", predicates

    # Priority 5: Retest Zone Pullback
    if p_retest_touch and p_retest_close and not p_wick_breach and p_retest_vol:
        return "RETEST_PENDING", predicates

    # Priority 6: Retest High Volume Rejection
    if p_retest_touch and not p_retest_vol:
        return "STRUCTURE_INVALIDATED", predicates

    # Priority 7: Indecision / Stalled Fallback
    return "STALLED_PENDING", predicates


# ---------------------------------------------------------------------------
# Section 8: Complete Retest Lifecycle & Deterministic Bounce Logic
# ---------------------------------------------------------------------------

@dataclass
class RetestState:
    """State tracker across retest evaluation window t in [N+1, N+6] (§ 8).

    Note on Touch Semantics (§ 8.1 Step 2, Correction D):
    `touch_count` is a diagnostic count of qualifying individual candles satisfying
    Low_t <= R_level + 0.20 * ATR_ref within [N+1, N+6], not a count of independent
    support test episodes.
    """
    breakout_bar_index: int       # Bar N
    r_level: float
    atr_ref: float
    vol_baseline: float           # V_bar_20(N-1)
    current_bar_index: int        # Current bar t
    touch_count: int = 0
    lowest_retest_price: Optional[float] = None
    bounce_window_end: Optional[int] = None
    touch_bars: List[int] = field(default_factory=list)
    ref_high: Optional[float] = None
    status: str = "PENDING"       # "PENDING", "RETEST_PENDING", "RETEST_CONFIRMED", "STRUCTURE_INVALIDATED", "EXPIRED", "DATA_INVALID"
    invalidation_reason: Optional[str] = None
    last_processed_bar_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize complete state representation to dict (§ 8, Correction B)."""
        return {
            "breakout_bar_index": self.breakout_bar_index,
            "r_level": float(self.r_level),
            "atr_ref": float(self.atr_ref),
            "vol_baseline": float(self.vol_baseline),
            "current_bar_index": self.current_bar_index,
            "touch_count": self.touch_count,
            "lowest_retest_price": float(self.lowest_retest_price) if self.lowest_retest_price is not None else None,
            "bounce_window_end": self.bounce_window_end,
            "touch_bars": list(self.touch_bars),
            "ref_high": float(self.ref_high) if self.ref_high is not None else None,
            "status": self.status,
            "invalidation_reason": self.invalidation_reason,
            "last_processed_bar_index": self.last_processed_bar_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetestState":
        """Deserialize and strictly validate serialized RetestState dict (§ 8, Correction B, I)."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict for RetestState, got {type(data).__name__}")

        required_fields = [
            "breakout_bar_index",
            "r_level",
            "atr_ref",
            "vol_baseline",
            "current_bar_index",
            "touch_count",
            "lowest_retest_price",
            "bounce_window_end",
            "touch_bars",
            "ref_high",
            "status",
            "invalidation_reason",
        ]
        for field_name in required_fields:
            if field_name not in data:
                raise ValueError(f"Missing required field: '{field_name}'")

        # Validate bar indices
        b_idx = data["breakout_bar_index"]
        if isinstance(b_idx, bool) or not isinstance(b_idx, int) or b_idx < 0:
            raise ValueError(f"Invalid breakout_bar_index: {b_idx}")

        c_idx = data["current_bar_index"]
        if isinstance(c_idx, bool) or not isinstance(c_idx, int) or c_idx < b_idx:
            raise ValueError(f"Invalid current_bar_index {c_idx} < breakout_bar_index {b_idx}")

        # Validate structural floats
        def _check_positive_float(val: Any, name: str) -> float:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"{name} must be numeric, got {type(val).__name__}")
            f_val = float(val)
            if not math.isfinite(f_val) or f_val <= 0.0:
                raise ValueError(f"{name} must be positive and finite, got {val}")
            return f_val

        def _check_optional_positive_float(val: Any, name: str) -> Optional[float]:
            if val is None:
                return None
            return _check_positive_float(val, name)

        r_level = _check_positive_float(data["r_level"], "r_level")
        atr_ref = _check_positive_float(data["atr_ref"], "atr_ref")
        vol_baseline = _check_positive_float(data["vol_baseline"], "vol_baseline")
        lowest_retest_price = _check_optional_positive_float(data["lowest_retest_price"], "lowest_retest_price")
        ref_high = _check_optional_positive_float(data["ref_high"], "ref_high")

        # Validate touch count
        tc = data["touch_count"]
        if isinstance(tc, bool) or not isinstance(tc, int) or tc < 0:
            raise ValueError(f"Invalid touch_count: {tc}")

        # Validate touch_bars
        tb = data["touch_bars"]
        if not isinstance(tb, list):
            raise ValueError(f"touch_bars must be a list, got {type(tb).__name__}")

        parsed_touch_bars = []
        for i, bar in enumerate(tb):
            if isinstance(bar, bool) or not isinstance(bar, int):
                raise ValueError(f"touch_bars element {i} must be an integer, got {type(bar).__name__}")
            if bar < b_idx + 1 or bar > b_idx + 6:
                raise ValueError(f"touch_bars element {bar} outside lifecycle [N+1, N+6] ([{b_idx+1}, {b_idx+6}])")
            if parsed_touch_bars and bar <= parsed_touch_bars[-1]:
                raise ValueError(f"touch_bars must be strictly ordered and non-duplicate: {tb}")
            if bar > c_idx:
                raise ValueError(f"touch_bars element {bar} exceeds current_bar_index {c_idx}")
            parsed_touch_bars.append(bar)

        # Consistent touch history check
        if tc == 0:
            if parsed_touch_bars:
                raise ValueError(f"touch_bars must be empty when touch_count == 0, got {parsed_touch_bars}")
            if lowest_retest_price is not None:
                raise ValueError("lowest_retest_price must be None when touch_count == 0")
        else:
            if len(parsed_touch_bars) != tc:
                raise ValueError(f"Inconsistent touch history: touch_count={tc} but len(touch_bars)={len(parsed_touch_bars)}")
            if lowest_retest_price is None:
                raise ValueError("lowest_retest_price must not be None when touch_count > 0")

        # Validate bounce_window_end
        bwe = data["bounce_window_end"]
        if bwe is not None:
            if isinstance(bwe, bool) or not isinstance(bwe, int):
                raise ValueError(f"bounce_window_end must be int or None, got {type(bwe).__name__}")
            if bwe < b_idx + 1 or bwe > b_idx + 6:
                raise ValueError(f"bounce_window_end {bwe} outside permitted window [{b_idx+1}, {b_idx+6}]")
            if tc == 0:
                raise ValueError("bounce_window_end must be None when touch_count == 0")
            expected_bwe = min(parsed_touch_bars[-1] + 2, b_idx + 6)
            if bwe != expected_bwe:
                raise ValueError(f"bounce_window_end {bwe} does not match expected {expected_bwe} from touch_bars {parsed_touch_bars}")
        elif tc > 0:
            raise ValueError("bounce_window_end cannot be None when touch_count > 0")

        # Validate status
        valid_statuses = {
            "PENDING",
            "RETEST_PENDING",
            "RETEST_CONFIRMED",
            "STRUCTURE_INVALIDATED",
            "EXPIRED",
            "DATA_INVALID",
        }
        status = data["status"]
        if status not in valid_statuses:
            raise ValueError(f"Invalid status '{status}', must be one of {valid_statuses}")

        inv_reason = data.get("invalidation_reason")
        if inv_reason is not None and not isinstance(inv_reason, str):
            raise ValueError(f"invalidation_reason must be str or None, got {type(inv_reason).__name__}")

        # Cross-field consistency: terminal vs non-terminal invalidation_reason (§ Correction I)
        if status in ("STRUCTURE_INVALIDATED", "EXPIRED", "DATA_INVALID"):
            if inv_reason is None or not inv_reason.strip():
                raise ValueError(f"Terminal status '{status}' requires non-empty invalidation_reason")
        else:
            if inv_reason is not None:
                raise ValueError(f"Non-terminal status '{status}' cannot have invalidation_reason: {inv_reason}")

        # Cross-field consistency: status vs touch history (§ Correction I)
        if status == "PENDING":
            if tc != 0:
                raise ValueError(f"Status 'PENDING' requires touch_count == 0, got {tc}")
            if ref_high is not None:
                raise ValueError(f"Status 'PENDING' cannot have ref_high, got {ref_high}")
        elif status == "RETEST_PENDING":
            if tc < 1:
                raise ValueError(f"Status 'RETEST_PENDING' requires touch_count >= 1, got {tc}")
        elif status == "RETEST_CONFIRMED":
            if tc < 1:
                raise ValueError(f"Status 'RETEST_CONFIRMED' requires touch_count >= 1, got {tc}")
            if ref_high is None:
                raise ValueError("Status 'RETEST_CONFIRMED' requires non-None ref_high")

        # Cross-field consistency: active status lifecycle bounds (§ Correction I)
        if status in ("PENDING", "RETEST_PENDING", "RETEST_CONFIRMED"):
            if c_idx > b_idx + 6:
                raise ValueError(f"Active status '{status}' cannot exceed maximum lifecycle bar N+6 ({b_idx + 6}), got {c_idx}")

        # Validate last_processed_bar_index and cross-field consistency (§ Correction I)
        last_proc = data.get("last_processed_bar_index")
        if last_proc is not None:
            if isinstance(last_proc, bool) or not isinstance(last_proc, int) or last_proc < b_idx:
                raise ValueError(f"Invalid last_processed_bar_index: {last_proc}")
        else:
            last_proc = c_idx if c_idx > b_idx else None

        if c_idx > b_idx:
            if last_proc != c_idx:
                raise ValueError(f"last_processed_bar_index ({last_proc}) must equal current_bar_index ({c_idx})")
        else:
            if last_proc is not None and last_proc != b_idx:
                raise ValueError(f"last_processed_bar_index ({last_proc}) must be None or {b_idx} when current_bar_index == breakout_bar_index")

        return cls(
            breakout_bar_index=b_idx,
            r_level=r_level,
            atr_ref=atr_ref,
            vol_baseline=vol_baseline,
            current_bar_index=c_idx,
            touch_count=tc,
            lowest_retest_price=lowest_retest_price,
            bounce_window_end=bwe,
            touch_bars=parsed_touch_bars,
            ref_high=ref_high,
            status=status,
            invalidation_reason=inv_reason,
            last_processed_bar_index=last_proc,
        )


def evaluate_retest_candle(
    retest_state: RetestState,
    candle_t: Any,
    bar_index: int,
    delta_norm_t: float,
    all_retest_candles: Optional[Sequence[Any]],
    as_of_ms: Optional[int] = None,
) -> RetestState:
    """Evaluate processing precedence on closed retest candle t in [N+1, N+6] (§ 8.1).

    Unambiguous Processing Order (§ Correction A, G, H):
    1. Reject already-terminal states.
    2. Validate the incoming candle, chronological position, continuity, and idempotency.
    3. Check whether the retest's maximum lifecycle window has expired (bar_index > N+6).
    4. Check whether the bounce confirmation window has expired (bar_index > bounce_window_end).
    5. Apply structural invalidation rules (floor breach, wick breach, volume expansion).
    6. Register a valid touch (Low <= R_level + 0.20 * ATR_ref).
    7. Evaluate bounce confirmation only while its confirmation window remains valid.
    8. Apply final lifecycle expiry (bar_index >= N+6 without confirmation).
    """
    n = retest_state.breakout_bar_index

    # -----------------------------------------------------------------------
    # Step 1: Reject already-terminal states
    # -----------------------------------------------------------------------
    if retest_state.status in ("STRUCTURE_INVALIDATED", "RETEST_CONFIRMED", "EXPIRED", "DATA_INVALID"):
        return retest_state

    # -----------------------------------------------------------------------
    # Step 2: Validate incoming candle, chronological position, continuity, and idempotency
    # -----------------------------------------------------------------------
    if isinstance(bar_index, bool) or not isinstance(bar_index, int):
        retest_state.status = "DATA_INVALID"
        retest_state.invalidation_reason = f"Invalid bar_index type: {type(bar_index).__name__}"
        return retest_state

    # Bars at or before breakout bar N are out-of-order prior to retest window: do not mutate state
    if bar_index <= n:
        return retest_state

    # Chronological continuity & idempotency (§ Correction G):
    if retest_state.last_processed_bar_index is None:
        if bar_index > n + 1:
            retest_state.status = "DATA_INVALID"
            retest_state.invalidation_reason = f"Lifecycle discontinuity: initial bar {bar_index} > N+1 ({n + 1})"
            retest_state.current_bar_index = bar_index
            retest_state.last_processed_bar_index = bar_index
            return retest_state
    else:
        if bar_index == retest_state.last_processed_bar_index:
            # Duplicate bar: idempotent no-op
            return retest_state
        if bar_index < retest_state.last_processed_bar_index:
            # Out-of-order bar: safe no-op
            return retest_state
        if bar_index > retest_state.last_processed_bar_index + 1:
            # Missing intermediate lifecycle bar (e.g. N+1 then N+3 without N+2)
            retest_state.status = "DATA_INVALID"
            retest_state.invalidation_reason = f"Lifecycle discontinuity: bar {bar_index} arrived, expected {retest_state.last_processed_bar_index + 1}"
            retest_state.current_bar_index = bar_index
            retest_state.last_processed_bar_index = bar_index
            return retest_state

    c_t, rej = _extract_candle(candle_t, as_of_ms=as_of_ms)
    if c_t is None:
        retest_state.status = "DATA_INVALID"
        retest_state.invalidation_reason = f"Corrupt bar {bar_index}: {rej}"
        retest_state.current_bar_index = bar_index
        retest_state.last_processed_bar_index = bar_index
        return retest_state

    if isinstance(delta_norm_t, bool) or not isinstance(delta_norm_t, (int, float)) or not math.isfinite(delta_norm_t) or delta_norm_t < -1.0 or delta_norm_t > 1.0:
        retest_state.status = "DATA_INVALID"
        retest_state.invalidation_reason = f"Invalid delta_norm_t on bar {bar_index}: {delta_norm_t}"
        retest_state.current_bar_index = bar_index
        retest_state.last_processed_bar_index = bar_index
        return retest_state

    # Verify that bar_index maps to the expected position in all_retest_candles if provided
    expected_rel_idx = bar_index - (n + 1)
    if all_retest_candles is not None and 0 <= expected_rel_idx < len(all_retest_candles):
        expected_bar, _ = _extract_candle(all_retest_candles[expected_rel_idx], as_of_ms=as_of_ms)
        if expected_bar is not None and expected_bar[0] != c_t[0]:
            retest_state.status = "DATA_INVALID"
            retest_state.invalidation_reason = (
                f"Bar {bar_index} open time {c_t[0]} does not match all_retest_candles[{expected_rel_idx}] open time {expected_bar[0]}"
            )
            retest_state.current_bar_index = bar_index
            retest_state.last_processed_bar_index = bar_index
            return retest_state

    _, _, o_t, h_t, l_t, c_val, v_t, _, _ = c_t
    r_level = retest_state.r_level
    atr_ref = retest_state.atr_ref
    vol_baseline = retest_state.vol_baseline

    floor_limit = r_level - 0.10 * atr_ref
    wick_limit = r_level - 0.25 * atr_ref
    retest_zone_high = r_level + 0.20 * atr_ref

    # -----------------------------------------------------------------------
    # Step 3 (Spec § 8.1 Step 1): Apply structural invalidation rules
    # Evaluated strictly first among market predicates (§ Amendment v2.1.6 § 2)
    # -----------------------------------------------------------------------
    is_touching_now = (l_t <= retest_zone_high + 1e-9)
    touch_active = (retest_state.touch_count > 0) or is_touching_now

    if c_val < floor_limit - 1e-9:
        retest_state.status = "STRUCTURE_INVALIDATED"
        retest_state.invalidation_reason = f"Close {c_val:.4f} breached floor {floor_limit:.4f}"
        retest_state.current_bar_index = bar_index
        retest_state.last_processed_bar_index = bar_index
        return retest_state

    if l_t < wick_limit - 1e-9:
        retest_state.status = "STRUCTURE_INVALIDATED"
        retest_state.invalidation_reason = f"Low {l_t:.4f} breached wick limit {wick_limit:.4f}"
        retest_state.current_bar_index = bar_index
        retest_state.last_processed_bar_index = bar_index
        return retest_state

    if touch_active and v_t > 1.25 * vol_baseline + 1e-9:
        retest_state.status = "STRUCTURE_INVALIDATED"
        retest_state.invalidation_reason = f"Volume {v_t:.2f} exceeded 1.25 * baseline {1.25 * vol_baseline:.2f}"
        retest_state.current_bar_index = bar_index
        retest_state.last_processed_bar_index = bar_index
        return retest_state

    # -----------------------------------------------------------------------
    # Step 4 (Spec § 8.1 Step 2): Register a valid touch
    # -----------------------------------------------------------------------
    if is_touching_now and bar_index <= n + 6:
        retest_state.touch_count += 1
        if retest_state.lowest_retest_price is None:
            retest_state.lowest_retest_price = l_t
        else:
            retest_state.lowest_retest_price = min(retest_state.lowest_retest_price, l_t)

        retest_state.bounce_window_end = min(bar_index + 2, n + 6)
        retest_state.touch_bars.append(bar_index)
        if retest_state.status == "PENDING":
            retest_state.status = "RETEST_PENDING"

    # -----------------------------------------------------------------------
    # Step 5 (Spec § 8.1 Step 3): Evaluate bounce confirmation only while its confirmation window remains valid
    # -----------------------------------------------------------------------
    if retest_state.touch_count >= 1 and len(retest_state.touch_bars) > 0:
        first_touch_bar = retest_state.touch_bars[0]
        bounce_window_active = (
            retest_state.bounce_window_end is not None
            and bar_index <= retest_state.bounce_window_end
            and bar_index <= n + 6
        )
        if bar_index > first_touch_bar and bounce_window_active:
            prior_highs = []
            hist_valid = True
            if all_retest_candles is None:
                hist_valid = False
            else:
                for b_idx in range(first_touch_bar, bar_index):
                    rel_idx = b_idx - (n + 1)
                    if not (0 <= rel_idx < len(all_retest_candles)):
                        hist_valid = False
                        break
                    # Closed-candle contract strictly enforced: pass as_of_ms (§ Correction E)
                    parsed_bar, rej_hist = _extract_candle(all_retest_candles[rel_idx], as_of_ms=as_of_ms)
                    if parsed_bar is None:
                        # Forming or corrupt candle must not contribute to ref_high
                        hist_valid = False
                        break
                    prior_highs.append(parsed_bar[3])

            if hist_valid and prior_highs:
                ref_high = max(prior_highs)
                retest_state.ref_high = ref_high
                if c_val > ref_high + 1e-9 and delta_norm_t > 0.0 + 1e-9:
                    retest_state.status = "RETEST_CONFIRMED"
                    retest_state.current_bar_index = bar_index
                    retest_state.last_processed_bar_index = bar_index
                    return retest_state

    # -----------------------------------------------------------------------
    # Step 6 (Spec § 8.1 Step 4): Retest Expiry
    # -----------------------------------------------------------------------
    if retest_state.bounce_window_end is not None and bar_index > retest_state.bounce_window_end:
        retest_state.status = "EXPIRED"
        retest_state.invalidation_reason = (
            f"Exceeded bounce window (bar {bar_index} > {retest_state.bounce_window_end})"
        )
    elif bar_index > n + 6:
        retest_state.status = "EXPIRED"
        retest_state.invalidation_reason = (
            f"Bar {bar_index} exceeds maximum lifecycle window N+6 ({n + 6})"
        )
    elif bar_index >= n + 6 and retest_state.status != "RETEST_CONFIRMED":
        retest_state.status = "EXPIRED"
        retest_state.invalidation_reason = f"Reached max bar {n+6} without confirmation"

    retest_state.current_bar_index = bar_index
    retest_state.last_processed_bar_index = bar_index
    return retest_state
