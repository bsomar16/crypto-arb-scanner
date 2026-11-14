"""Chronological Spot Long Signal Engine v9 (Pine Script v6 parity).

This module implements the exact logic of Spot Long Signal Engine v9:
- 5 core components (Trend, Momentum, Volume, Structure, Context) + Early detection.
- Dynamic scoring weights and volatility-adaptive threshold (sConf = baseConf + volatility_adj).
- S/R Quality Engine v3: volume-weighted, wick rejection counts, exponential decay (0.995),
  deduplication, max 30 levels, and minimum quality score filtering (1.5).
- Structure-based targets (R-ladder 1.5R/2.5R/4R/6R/8R snapped down to resistance levels).
- 9 setups: BRK (0), BNC (1), PB (2), TRD (3), EARLY_BRK (4), EARLY_REV (5),
  EARLY_VWAP (6), EARLY_REL (7), EARLY_MOM (8).
- Setup performance tracking and shrinkage pause rule.
- SPOT BUY ONLY; no futures, margin, shorting, or order execution.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple, Any

from sls_pine_math import (
    pine_ema, pine_rma, pine_rsi, pine_macd, pine_atr, pine_vwap,
    confirmed_pivots, delayed_highest, delayed_lowest, roc
)

BASE_CONFIDENCE = {"Conservative": 78, "Balanced": 70, "Aggressive": 62}
SETUP_NAMES = (
    "BRK", "BNC", "PB", "TRD",
    "EARLY_BRK", "EARLY_REV", "EARLY_VWAP", "EARLY_REL", "EARLY_MOM"
)


class SREngineV3:
    """Stateful S/R Engine matching Pine v9 f_addLevelV2, f_decayLevels, f_countRejections."""

    def __init__(self, tol: float = 0.2, decay: float = 0.995, max_age: int = 500,
                 rej_wick_ratio: float = 0.3, htf_merge_tol: float = 0.2,
                 min_qual: float = 1.5, max_levels: int = 30):
        self.c_srTol = tol
        self.c_srDecay = decay
        self.c_srMaxAge = max_age
        self.c_rejWickRatio = rej_wick_ratio
        self.c_htfMergeTol = htf_merge_tol
        self.c_srMinQual = min_qual
        self.c_srMaxLv = max_levels
        
        self.srLv: List[float] = []
        self.srTch: List[float] = []
        self.srRej: List[float] = []
        self.srVol: List[float] = []
        self.srBar: List[int] = []
        self.srSrc: List[int] = []    # 0=pivot, 1=HTF, 2=rejection
        self.srOrg: List[int] = []
        self.srQual: List[float] = []

    def decay_levels(self, bar_index: int):
        for i in range(len(self.srLv) - 1, -1, -1):
            self.srTch[i] *= self.c_srDecay
            self.srRej[i] *= self.c_srDecay
            self.srQual[i] *= self.c_srDecay
            if (bar_index - self.srBar[i] > self.c_srMaxAge or
                    self.srTch[i] + self.srRej[i] < 0.5 or
                    self.srQual[i] < 0.5):
                self._remove_at(i)

    def count_rejections(self, bar_index: int, open_p: float, high_p: float,
                         low_p: float, close_p: float):
        rng = high_p - low_p
        if rng <= 0 or not self.srLv:
            return
        low_wick = min(open_p, close_p) - low_p
        up_wick = high_p - max(open_p, close_p)
        for i in range(len(self.srLv)):
            lv = self.srLv[i]
            if lv <= close_p and lv >= low_p and (low_wick / rng) > self.c_rejWickRatio:
                self.srRej[i] += 1.0
                self.srBar[i] = bar_index
                self.srQual[i] += 0.2
            if lv >= close_p and lv <= high_p and (up_wick / rng) > self.c_rejWickRatio:
                self.srRej[i] += 1.0
                self.srBar[i] = bar_index
                self.srQual[i] += 0.2

    def add_level_v2(self, px: float, src_type: int, org_bar: int, vol_at_pivot: float,
                     bar_index: int, atr_n: float) -> int:
        if atr_n <= 0:
            return -1
        tol = self.c_srTol * atr_n
        hit = -1
        best = tol
        for i in range(len(self.srLv)):
            d = abs(self.srLv[i] - px)
            if d <= best:
                best = d
                hit = i
        if hit >= 0:
            n = self.srTch[hit]
            self.srLv[hit] = (self.srLv[hit] * n + px) / (n + 1.0)
            self.srTch[hit] = n + 1.0
            if src_type == 2:
                self.srRej[hit] += 1.0
            vw = self.srVol[hit]
            self.srVol[hit] = (vw * n + vol_at_pivot) / (n + 1.0)
            self.srBar[hit] = bar_index
            self.srQual[hit] = self.srTch[hit] + self.srRej[hit] * 0.5 + self.srVol[hit] * 0.1
        else:
            merge_thresh = 0.15 * atr_n if atr_n > 0 else 0.001 * px
            merge_idx = -1
            for i in range(len(self.srLv)):
                if abs(self.srLv[i] - px) <= merge_thresh:
                    merge_idx = i
                    break
            if merge_idx >= 0:
                n = self.srTch[merge_idx]
                self.srLv[merge_idx] = (self.srLv[merge_idx] * n + px) / (n + 1.0)
                self.srTch[merge_idx] = n + 1.0
                if src_type == 2:
                    self.srRej[merge_idx] += 1.0
                if src_type == 1 and self.srSrc[merge_idx] != 1:
                    self.srSrc[merge_idx] = 1
                    self.srTch[merge_idx] += 5.0
                vw = self.srVol[merge_idx]
                self.srVol[merge_idx] = (vw * n + vol_at_pivot) / (n + 1.0)
                self.srBar[merge_idx] = bar_index
                self.srQual[merge_idx] = self.srTch[merge_idx] + self.srRej[merge_idx] * 0.5 + self.srVol[merge_idx] * 0.1
                hit = merge_idx
            else:
                self.srLv.append(px)
                self.srTch.append(1.0)
                self.srRej.append(1.0 if src_type == 2 else 0.0)
                self.srVol.append(vol_at_pivot)
                self.srBar.append(bar_index)
                self.srSrc.append(src_type)
                self.srOrg.append(org_bar)
                self.srQual.append(1.0 + (5.0 if src_type == 1 else 0.0))
                hit = len(self.srLv) - 1
                if len(self.srLv) > self.c_srMaxLv:
                    w = min(range(len(self.srLv)), key=lambda k: self.srQual[k])
                    self._remove_at(w)
                    if hit > w:
                        hit -= 1
        return hit

    def _remove_at(self, idx: int):
        self.srLv.pop(idx)
        self.srTch.pop(idx)
        self.srRej.pop(idx)
        self.srVol.pop(idx)
        self.srBar.pop(idx)
        self.srSrc.pop(idx)
        self.srOrg.pop(idx)
        self.srQual.pop(idx)

    def nearest_v2(self, px: float, close_prev: float, close_curr: float) -> Tuple[Optional[float], int, Optional[float], int, bool]:
        sup = None
        res = None
        s_touch = 0
        r_touch = 0
        brk = False
        for i in range(len(self.srLv)):
            l = self.srLv[i]
            q = self.srQual[i]
            if q >= self.c_srMinQual:
                if l <= px:
                    if sup is None or l > sup:
                        sup = l
                        s_touch = int(q)
                else:
                    if res is None or l < res:
                        res = l
                        r_touch = int(q)
                if close_curr > l and close_prev <= l:
                    brk = True
        return sup, s_touch, res, r_touch, brk

    def next_resistances(self, htf_levels: List[float], price: float, count: int, atr_val: float) -> List[float]:
        all_lv = []
        for i in range(len(self.srLv)):
            if self.srQual[i] >= self.c_srMinQual:
                all_lv.append(self.srLv[i])
        for h in htf_levels:
            if h is not None and not math.isnan(h):
                all_lv.append(h)
        all_lv.sort()
        result = []
        dedup_thresh = 0.2 * atr_val if atr_val > 0 else 0.001 * price
        for lv in all_lv:
            if lv > price:
                is_dup = False
                if result:
                    if abs(lv - result[-1]) <= dedup_thresh:
                        is_dup = True
                if not is_dup:
                    result.append(lv)
                    if len(result) >= count:
                        break
        return result


class SetupTracker:
    """Per-setup performance tracker matching Pine v9 f_record and f_setupOn."""

    def __init__(self, min_trades_pause: int = 10, shrink_weight: int = 10,
                 pause_threshold: float = -0.1, pause_bars: int = 150):
        self.min_trades = min_trades_pause
        self.shrink_weight = shrink_weight
        self.pause_threshold = pause_threshold
        self.pause_bars = pause_bars
        
        self.stN = [0] * 9
        self.stW = [0] * 9
        self.stR = [0.0] * 9
        self.stLast = [-999] * 9

    def record(self, setup_idx: int, r_mult: float, bar_index: int):
        if 0 <= setup_idx < 9:
            self.stN[setup_idx] += 1
            if r_mult > 0:
                self.stW[setup_idx] += 1
            self.stR[setup_idx] += r_mult
            self.stLast[setup_idx] = bar_index

    def global_avg_r(self) -> float:
        total_n = sum(self.stN)
        return (sum(self.stR) / total_n) if total_n > 0 else 0.0

    def is_setup_on(self, setup_idx: int, bar_index: int) -> bool:
        if setup_idx < 0 or setup_idx >= 9:
            return True
        count = self.stN[setup_idx]
        if count < self.min_trades:
            return True
        global_avg = self.global_avg_r()
        w = self.shrink_weight
        shrunk = (self.stR[setup_idx] / count) * (count / (count + w)) + global_avg * (w / (count + w))
        is_paused = shrunk < self.pause_threshold and (bar_index - self.stLast[setup_idx] < self.pause_bars)
        return not is_paused


def evaluate_series(rows: List[Dict[str, Any]], interval: str,
                    htf_rows: Optional[List[Dict[str, Any]]] = None,
                    btc_rows: Optional[List[Dict[str, Any]]] = None,
                    btc_htf_rows: Optional[List[Dict[str, Any]]] = None,
                    sensitivity: str = "Balanced",
                    min_score: Optional[float] = None,
                    setup_tracker: Optional[SetupTracker] = None,
                    tf_a_rows: Optional[List[Dict[str, Any]]] = None,
                    tf_b_rows: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Evaluate closed Binance Spot candles chronologically using Pine v9 rules.

    Emits only a confirmed SPOT BUY signal on the latest closed candle.
    """
    if len(rows) < 205:
        return None

    tf_minutes = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30, "45m": 45,
        "1h": 60, "4h": 240, "1d": 1440, "1w": 10080
    }.get(interval)
    if tf_minutes is None:
        return None

    tf_small = tf_minutes <= 5
    tf_tiny = tf_minutes <= 1
    tf_big = tf_minutes >= 1440
    tf_huge = tf_minutes >= 10080
    htf_skip = tf_minutes >= 10080

    pivot_len = 3 if tf_tiny else 4 if tf_small else 5
    pivot_len_early = 2 if (tf_tiny or tf_small) else 3
    sr_len = 8 if tf_small else 5 if tf_big else 10
    sep_req = 0.0010 if tf_tiny else 0.0015 if tf_small else 0.0040 if tf_big else 0.0025
    c_max_slp = 30.0 if tf_big else 6.5
    c_pos_life = 12 if tf_huge else 40 if tf_big else 120
    ext_max_atr = 5.5 if tf_big else 4.5 if tf_small else 3.5
    ext_max_rsi = 92.0 if tf_big else 90.0 if tf_small else 82.0
    c_turn = 150000.0
    c_rr_min = 1.25
    c_stop_buf = 0.3
    c_tp_buf = 0.1
    c_stop_min_atr = 1.0
    c_stop_max_atr = 2.5

    base_conf = BASE_CONFIDENCE.get(str(sensitivity).title(), 70)
    tracker = setup_tracker or SetupTracker()

    # Extract OHLCVQ series
    o = [float(x["o"]) for x in rows]
    h = [float(x["h"]) for x in rows]
    l = [float(x["l"]) for x in rows]
    c = [float(x["c"]) for x in rows]
    v = [float(x["v"]) for x in rows]
    q = [float(x.get("q", x["v"] * x["c"])) for x in rows]
    t = [int(x["t"]) for x in rows]
    n = len(rows)
    i = n - 1

    # Indicator series
    atrs = pine_atr(h, l, c, 14)
    eF_series = pine_ema(c, 20)
    eM_series = pine_ema(c, 50)
    eS_series = pine_ema(c, 200)
    eF9_series = pine_ema(c, 9)
    eF21_series = pine_ema(c, 21)
    rsi14_series = pine_rsi(c, 14)
    macdL_series, sigL_series, histL_series = pine_macd(c, 12, 26, 9)
    hlc3_series = [(high_val + low_val + close_val) / 3.0 for high_val, low_val, close_val in zip(h, l, c)]
    vwap_series = pine_vwap(hlc3_series, v, t)

    if any(x is None for x in (eF_series[i], eM_series[i], eS_series[i],
                              rsi14_series[i], macdL_series[i], sigL_series[i],
                              histL_series[i], atrs[i])):
        return None

    atr = float(atrs[i])
    price = c[i]
    if atr <= 0 or i < 120:
        return None

    # HTF trend alignment
    htf_a50_ok = False
    htf_align_ok = False
    if htf_skip:
        htf_a50_ok = htf_align_ok = True
    elif htf_rows and len(htf_rows) >= 50:
        hc = [float(x["c"]) for x in htf_rows]
        he20 = pine_ema(hc, 20)
        he50 = pine_ema(hc, 50)
        if he20[-1] is not None and he50[-1] is not None:
            htf_a50_ok = hc[-1] > he50[-1]
            htf_align_ok = he20[-1] > he50[-1]
    else:
        # Intraday requires HTF context
        return None
    htf_bull = htf_a50_ok and htf_align_ok

    # BTC benchmark
    btc_avail = False
    btc_c = None
    coin_roc20 = roc(c, 20, i) or 0.0
    btc_roc20 = 0.0
    rel_diff = 0.0
    rel_strong = False
    coin_roc5 = roc(c, 5, i) or 0.0
    btc_roc5 = 0.0
    rel_strength5 = 0.0
    rel_strength_surge = False

    if btc_rows and len(btc_rows) >= 20:
        btc_c_series = [float(x["c"]) for x in btc_rows]
        btc_c = btc_c_series[-1]
        btc_avail = True
        btc_roc20 = roc(btc_c_series, 20, len(btc_c_series) - 1) or 0.0
        rel_diff = coin_roc20 - btc_roc20
        rel_strong = rel_diff >= 0.0
        btc_roc5 = roc(btc_c_series, 5, len(btc_c_series) - 1) or 0.0
        rel_strength5 = coin_roc5 - btc_roc5
        rel_strength_surge = btc_avail and rel_strength5 >= 2.0 and c[i] > o[i]
    elif not htf_skip:
        # Benchmark data required
        return None

    # BTC HTF state (B_SUP, B_WEAK, B_BEAR, B_NA)
    bench = "B_NA"
    btc_bench_rows = btc_htf_rows or btc_rows
    if btc_bench_rows and len(btc_bench_rows) >= 200:
        bc = [float(x["c"]) for x in btc_bench_rows]
        b50 = pine_ema(bc, 50)
        b200 = pine_ema(bc, 200)
        if b50[-1] is not None and b200[-1] is not None:
            if bc[-1] > b50[-1] and bc[-1] > b200[-1]:
                bench = "B_SUP"
            elif bc[-1] < b50[-1] and bc[-1] < b200[-1]:
                bench = "B_BEAR"
            else:
                bench = "B_WEAK"
    bench_ok = (not btc_avail) or (bench != "B_NA")

    # HTF levels: previous high/low from tfA and tfB
    htf_lv = []
    if tf_a_rows and len(tf_a_rows) >= 2:
        htf_lv.extend([float(tf_a_rows[-2]["h"]), float(tf_a_rows[-2]["l"])])
    if tf_b_rows and len(tf_b_rows) >= 2:
        htf_lv.extend([float(tf_b_rows[-2]["h"]), float(tf_b_rows[-2]["l"])])

    # Moving averages & regime
    eF = float(eF_series[i])
    eM = float(eM_series[i])
    eS = float(eS_series[i])
    rsi14 = float(rsi14_series[i])
    macdL = float(macdL_series[i])
    sigL = float(sigL_series[i])
    histL = float(histL_series[i])
    histL_prev = float(histL_series[i - 1]) if histL_series[i - 1] is not None else histL

    sep_fm = (eF - eM) / eM if eM > 0 else 0.0
    sep_ms = (eM - eS) / eS if eS > 0 else 0.0
    aligned = eF > eM > eS and sep_fm >= sep_req and sep_ms >= sep_req
    above_s = price > eS
    lowest3p = min(c[i - 3 : i]) if i >= 3 else c[i]
    rsi_rising = rsi14 > (rsi14_series[i - 1] or rsi14)
    hist_rising = histL > histL_prev
    roc5 = roc(c, 5, i) or 0.0
    roc10 = roc(c, 10, i) or 0.0

    full = aligned and above_s and price > eF and rsi14 > 50
    cont = aligned and above_s and price > eF and lowest3p < eF and rsi_rising and hist_rising
    rev = (not (aligned and above_s)) and price > eF and rsi14 > 45 and rsi_rising and hist_rising and roc5 > 0
    bear = price < eS and rsi14 < 45 and eM < eS and not rev

    if full:
        regime = "R_BULL"
    elif cont:
        regime = "R_CONT"
    elif rev:
        regime = "R_REV"
    elif bear:
        regime = "R_BEAR"
    else:
        regime = "R_SIDE"
    regime_ok = regime in ("R_BULL", "R_CONT", "R_REV")

    # Extension and Liquidity
    ext_atr = (price - eF) / atr
    ext_high = ext_atr >= ext_max_atr or rsi14 >= ext_max_rsi
    avg_vol_20 = sum(v[i - 19 : i + 1]) / 20.0 if i >= 19 else sum(v) / len(v)
    rvz = (v[i] / avg_vol_20) if avg_vol_20 > 0 else 0.0
    turn = v[i] * price
    liq_ok = (c_turn <= 0) or (turn >= c_turn)

    # Efficiency ratio (20 bars)
    er_den = sum(abs(c[k] - c[k - 1]) for k in range(max(1, i - 19), i + 1))
    er = (abs(c[i] - c[max(0, i - 20)]) / er_den) if er_den > 0 else 0.0

    # Confirmed Pivots and Structure
    lows = confirmed_pivots(l, pivot_len, pivot_len, low_pivot=True)
    highs = confirmed_pivots(h, pivot_len, pivot_len, low_pivot=False)

    pl_confirmed_bars = [p + pivot_len for p, val in lows]
    ph_confirmed_bars = [p + pivot_len for p, val in highs]

    known_lows = [val for p, val in lows if p + pivot_len <= i]
    known_highs = [val for p, val in highs if p + pivot_len <= i]

    hl_struct = len(known_lows) >= 2 and known_lows[-1] > known_lows[-2]
    hh_struct = len(known_highs) >= 2 and known_highs[-1] > known_highs[-2]

    # Chronological BOS detection
    last_sw_h = None
    last_bos_bar = -999
    bos_history = []
    for bar in range(n):
        # Update swing high when confirmed
        for p, val in highs:
            if p + pivot_len == bar:
                last_sw_h = val
        if last_sw_h is not None and c[bar] > last_sw_h and c[bar] > o[bar] and (bar - last_bos_bar >= 5):
            last_bos_bar = bar
            bos_history.append(bar)
    bos_up_recent = (last_bos_bar > 0) and (i - last_bos_bar <= 10)

    # S/R Engine V3 chronological processing
    sr_engine = SREngineV3(
        tol=0.2, decay=0.995, max_age=500, rej_wick_ratio=0.3,
        htf_merge_tol=0.2, min_qual=1.5, max_levels=30
    )

    sr_pivots_h = confirmed_pivots(h, sr_len, sr_len, low_pivot=False)
    sr_pivots_l = confirmed_pivots(l, sr_len, sr_len, low_pivot=True)
    sr_h_dict = {p + sr_len: val for p, val in sr_pivots_h}
    sr_l_dict = {p + sr_len: val for p, val in sr_pivots_l}

    for bar in range(n):
        sr_engine.decay_levels(bar)
        bar_atr = atrs[bar] or atr
        if bar_atr > 0:
            if bar in sr_h_dict:
                sr_engine.add_level_v2(sr_h_dict[bar], 0, bar - sr_len, v[bar], bar, bar_atr)
            if bar in sr_l_dict:
                sr_engine.add_level_v2(sr_l_dict[bar], 0, bar - sr_len, v[bar], bar, bar_atr)
            # Merge HTF levels
            if htf_lv and bar >= 100:
                for hlv in htf_lv:
                    merged = False
                    htf_tol = 0.2 * bar_atr
                    for j in range(len(sr_engine.srLv)):
                        if abs(sr_engine.srLv[j] - hlv) <= htf_tol:
                            sr_engine.srSrc[j] = 1
                            sr_engine.srTch[j] += 5.0
                            merged = True
                            break
                    if not merged:
                        sr_engine.add_level_v2(hlv, 1, -1, v[bar] * 2.0, bar, bar_atr)
            sr_engine.count_rejections(bar, o[bar], h[bar], l[bar], c[bar])

    sup_lv, sup_touch, res_lv, res_touch, sr_brk = sr_engine.nearest_v2(
        price, c[i - 1], c[i]
    )

    # Early Bullish Detection (Lines 630-691)
    vol_surge_ratio = (v[i] / avg_vol_20) if avg_vol_20 > 0 else 0.0
    vol_surge = vol_surge_ratio >= 2.0 and c[i] > o[i]

    roc5_prev = roc(c, 5, i - 1) or 0.0
    roc10_prev = roc(c, 10, i - 1) or 0.0
    mom_accel = roc5 > roc5_prev and roc10 > roc10_prev and roc5 > 0

    hist_slope = histL - histL_prev
    hist_slope_prev = (histL_prev - (float(histL_series[i - 2]) if i >= 2 and histL_series[i - 2] is not None else histL_prev))
    macd_accel = hist_slope > hist_slope_prev and histL > 0

    body_size = abs(c[i] - o[i])
    lower_wick = min(o[i], c[i]) - l[i]
    upper_wick = h[i] - max(o[i], c[i])
    hammer = lower_wick >= 2.0 * body_size and upper_wick <= 0.1 * body_size and c[i] > o[i]
    bullish_engulf = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]
    piercing_line = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] > (o[i - 1] + c[i - 1]) / 2.0 and c[i] < o[i - 1]

    high5 = max(h[max(0, i - 5) : i]) if i >= 1 else h[i]
    high10 = max(h[max(0, i - 10) : i]) if i >= 1 else h[i]
    early_break5 = c[i] > high5 and rvz >= 1.5 and c[i] > o[i]
    early_break10 = c[i] > high10 and rvz >= 1.3 and c[i] > o[i]

    vwap_val = vwap_series[i]
    vwap_prev = vwap_series[i - 1]
    vwap_reclaim = (vwap_val is not None and vwap_prev is not None and
                    c[i] > vwap_val and c[i - 1] <= vwap_prev and rvz >= 1.2)

    eF9_val = eF9_series[i]
    eF21_val = eF21_series[i]
    eF9_prev = eF9_series[i - 1]
    ema_fan_bull = (eF9_val is not None and eF21_val is not None and eF9_prev is not None and
                    eF9_val > eF21_val and c[i] > eF9_val and eF9_val > eF9_prev)

    c_early = 0.0
    if vol_surge: c_early += 5.0
    if mom_accel: c_early += 4.0
    if macd_accel: c_early += 3.0
    if hammer or bullish_engulf or piercing_line: c_early += 4.0
    if early_break5 or early_break10: c_early += 5.0
    if vwap_reclaim: c_early += 3.0
    if ema_fan_bull: c_early += 2.0
    if rel_strength_surge: c_early += 2.0
    c_early = min(c_early, 25.0)

    early_trigger = c_early >= 18.0
    if early_trigger:
        if vol_surge and early_break5:
            early_setup = 4; early_tag = "EARLY_BRK"
        elif hammer or bullish_engulf or piercing_line:
            early_setup = 5; early_tag = "EARLY_REV"
        elif vwap_reclaim and ema_fan_bull:
            early_setup = 6; early_tag = "EARLY_VWAP"
        elif rel_strength_surge:
            early_setup = 7; early_tag = "EARLY_REL"
        elif mom_accel and macd_accel:
            early_setup = 8; early_tag = "EARLY_MOM"
        else:
            early_setup = 4; early_tag = "EARLY_BRK"
    else:
        early_setup = -1
        early_tag = ""

    # Standard Setups (0=BRK, 1=BNC, 2=PB, 3=TRD)
    rng_bar = h[i] - l[i]
    close_pos = ((c[i] - l[i]) / rng_bar) if rng_bar > 0 else 0.5
    bull_bar = c[i] > o[i] and close_pos >= 0.6
    trend_reg = regime in ("R_BULL", "R_CONT")
    lowest3_low = min(l[i - 2 : i + 1]) if i >= 2 else l[i]

    res20 = delayed_highest(h, 20, 1, i)
    brk_cond = bool((sr_brk or (res20 is not None and c[i] > res20)) and
                    bull_bar and rvz >= 1.2 and (c[i] - o[i]) >= 0.4 * atr)

    bnc_conf = c[i] > o[i] and ((rng_bar > 0 and (lower_wick / rng_bar) >= 0.5) or
                                (c[i] > h[i - 1] and close_pos >= 0.6))
    trg_bnc = bool(sup_lv is not None and lowest3_low <= sup_lv + 0.35 * atr and
                   c[i] > sup_lv and bnc_conf and rsi14 < 60)

    trg_pb = bool(trend_reg and lowest3_low <= eF + 0.25 * atr and c[i] > eF and
                  bull_bar and c[i] > h[i - 1] and rsi_rising and rsi14 < 70)

    trg_thr = bool(trend_reg and bull_bar and c[i] > h[i - 1] and rvz >= 1.0 and
                   c[i] > eF and histL > histL_prev)

    use_brk = brk_cond and tracker.is_setup_on(0, i)
    use_bnc = trg_bnc and tracker.is_setup_on(1, i)
    use_pb = trg_pb and tracker.is_setup_on(2, i)
    use_thr = trg_thr and tracker.is_setup_on(3, i)
    use_early = early_trigger  # Early setups bypass pause check

    trigger = use_brk or use_bnc or use_pb or use_thr or use_early
    if not trigger:
        return None

    if use_brk:
        setup_idx = 0; setup_tag = "BRK"
    elif use_bnc:
        setup_idx = 1; setup_tag = "BNC"
    elif use_pb:
        setup_idx = 2; setup_tag = "PB"
    elif use_thr:
        setup_idx = 3; setup_tag = "TRD"
    else:
        setup_idx = early_setup; setup_tag = early_tag

    # Dynamic Scoring System (5 core components)
    vol_score = atr / price * 10000.0
    dyn_threshold = base_conf + (5 if vol_score > 20 else -3 if vol_score < 5 else 0)

    # 1. Trend (0-20 pts)
    c_trend = 0.0
    if price > eF and eF > eM: c_trend += 8.0
    if eM > eS: c_trend += 5.0
    c_trend += 7.0 if regime == "R_BULL" else 5.0 if regime == "R_CONT" else 3.0 if regime == "R_REV" else 0.0

    # 2. Momentum (0-20 pts)
    c_momentum = 0.0
    if macdL > sigL and histL > histL_prev: c_momentum += 8.0
    if roc10 > 0 and roc5 > 0: c_momentum += 7.0
    if 50 < rsi14 < 75 and rsi_rising: c_momentum += 5.0

    # 3. Volume (0-20 pts)
    c_volume = 0.0
    c_volume += 8.0 if rvz >= 1.5 else 5.0 if rvz >= 1.2 else 3.0 if rvz >= 1.0 else 0.0
    up_vol = sum(v[k] for k in range(max(0, i - 19), i + 1) if c[k] > o[k])
    tot_vol = sum(v[max(0, i - 19) : i + 1])
    up_share = (up_vol / tot_vol) if tot_vol > 0 else 0.0
    c_volume += 6.0 if up_share > 0.6 else 4.0 if up_share > 0.5 else 0.0
    green3 = sum(1 for k in range(max(0, i - 2), i + 1) if c[k] > o[k])
    if green3 >= 2 and c[i] > o[i]: c_volume += 6.0

    # 4. Structure (0-20 pts)
    c_struct = 0.0
    if sup_lv is not None and (price - sup_lv) <= 1.0 * atr: c_struct += 6.0
    if hl_struct: c_struct += 5.0
    if hh_struct: c_struct += 4.0
    if sr_brk or bos_up_recent: c_struct += 5.0

    # 5. Context (0-20 pts)
    c_context = 0.0
    c_context += 6.0 if (htf_skip or (htf_a50_ok and htf_align_ok)) else 3.0 if htf_a50_ok else 0.0
    c_context += 5.0 if bench == "B_SUP" else 2.0 if bench == "B_WEAK" else 0.0
    if regime_ok: c_context += 4.0
    if not ext_high: c_context += 3.0
    if liq_ok: c_context += 2.0

    # Dynamic weights
    total_trades = sum(tracker.stN)
    if total_trades >= 20:
        w_trend, w_momentum, w_volume, w_struct, w_context, w_early = 1.1, 1.0, 0.9, 1.2, 0.8, 1.0
    else:
        w_trend = w_momentum = w_volume = w_struct = w_context = w_early = 1.0

    raw_score = (c_trend * w_trend + c_momentum * w_momentum + c_volume * w_volume +
                 c_struct * w_struct + c_context * w_context + c_early * w_early)
    score = max(0.0, min(100.0, raw_score))
    s_conf = int(dyn_threshold)
    threshold = float(min_score if min_score is not None else s_conf)

    # Structure Stop / Room / R:R
    entry_ref = price
    stop_lvl = (sup_lv - c_stop_buf * atr) if sup_lv is not None else (entry_ref - 1.5 * atr)
    stop_lvl = min(stop_lvl, entry_ref - c_stop_min_atr * atr)
    stop_lvl = max(stop_lvl, entry_ref - c_stop_max_atr * atr)
    stop_dist = entry_ref - stop_lvl
    stop_pct = (stop_dist / entry_ref * 100.0) if entry_ref > 0 else 0.0
    stop_ok = atr > 0 and stop_dist > 0 and stop_pct <= c_max_slp

    # Next resistances from S/R Engine
    next_res = sr_engine.next_resistances(htf_lv, entry_ref, 5, atr)
    room_r = ((next_res[0] - entry_ref) / stop_dist) if (next_res and stop_dist > 0) else None
    rr_eff = min(8.0, room_r) if room_r is not None else 4.0
    rr_ok = rr_eff >= c_rr_min and stop_ok

    # Mandatory Gates (9)
    m_hist = i > (50 if tf_big else 120)
    m_score = score >= threshold
    m_reg = regime_ok
    m_bench = bench_ok
    m_liq = liq_ok
    m_ext = not ext_high
    m_rr = stop_ok and rr_ok
    m_trig = trigger
    m_chop = (er >= 0.15) or use_brk or use_bnc or use_early

    gates = {
        "history": m_hist, "score": m_score, "regime": m_reg, "benchmark": m_bench,
        "liquidity": m_liq, "extension": m_ext, "risk_reward": m_rr,
        "trigger": m_trig, "chop": m_chop
    }

    if not all(gates.values()):
        return None

    # Calculate 5 structure-based targets
    targets = []
    prev_tp = entry_ref
    mults = (1.5, 2.5, 4.0, 6.0, 8.0)
    for k, mult in enumerate(mults):
        ladder = entry_ref + mult * stop_dist
        tpv = ladder
        if next_res:
            for cand_res in next_res:
                cand = cand_res - c_tp_buf * atr
                if cand > prev_tp + 0.25 * stop_dist and cand < ladder:
                    tpv = cand
                    break
        targets.append(tpv)
        prev_tp = tpv

    potential_pct = (targets[2] / entry_ref - 1.0) * 100.0

    return {
        "coin": rows[-1].get("symbol"),
        "interval": interval,
        "strategy": "SLS",
        "signal_source": "SLS",
        "signal_engine": "Spot Long Signal Engine v9",
        "strategy_family": "SLS",
        "signal_action": "BUY",
        "entry_trigger": f"SLS_{setup_tag}",
        "setup_type": setup_tag,
        "setup_index": setup_idx,
        "candle_open_time": rows[-1]["t"],
        "price": price,
        "entry": entry_ref,
        "stop": stop_lvl,
        "t1": targets[0],
        "t2": targets[1],
        "t3": targets[2],
        "t4": targets[3],
        "t5": targets[4],
        "target": targets[2],
        "potential_pct": round(potential_pct, 2),
        "modelled_potential_pct": round(potential_pct, 2),
        "atr": round(atr, 6),
        "score": round(score, 1),
        "st": round(score, 1),
        "sls_threshold": threshold,
        "sls_dyn_threshold": dyn_threshold,
        "vol_score": round(vol_score, 2),
        "rsi": round(rsi14, 1),
        "vol_x": round(rvz, 2),
        "risk_pct": round(stop_pct, 2),
        "rr": round(rr_eff, 2),
        "sls_regime": regime,
        "sls_benchmark": bench,
        "sls_components": {
            "c_trend": round(c_trend, 1),
            "c_momentum": round(c_momentum, 1),
            "c_volume": round(c_volume, 1),
            "c_struct": round(c_struct, 1),
            "c_context": round(c_context, 1),
            "c_early": round(c_early, 1),
        },
        "sls_weights": {
            "w_trend": w_trend, "w_momentum": w_momentum, "w_volume": w_volume,
            "w_struct": w_struct, "w_context": w_context, "w_early": w_early,
        },
        "sls_gates": gates,
        "sls_support": sup_lv,
        "sls_resistance": res_lv,
        "sls_next_resistances": next_res,
        "sls_sensitivity": sensitivity,
        "risk_reward_ladder": targets,
    }

