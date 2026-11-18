"""Unit tests for Smart Money Phase 2 — Stage 1 (Breakout & Retest Engine).

Authoritative Engineering Baseline: SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md
Subsystem: smart_money/
Verification Scope:
- Category A: Causal Resistance Detection & Overhead Complete-Linkage Clustering (§ 2)
- Category B: Pre-Breakout Readiness Score S_ready (§ 3.2)
- Category C: Daily Macro Market Regime via SMA200 & Wilder's ADX14 (§ 3.3, § 13.2)
- Category D: Breakout Eligibility Gates G1-G7 (§ 4.1)
- Category E: Breakout Conviction Scoring S_break (§ 4.2)
- Category F: Bar N+1 Continuation Decision Hierarchy (§ 7, Examples 7-10)
- Category G: Multi-Candle Retest Lifecycle & Bounce Confirmation (§ 8, Example 11)
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from smart_money.breakout import (
    BreakoutGatesResult,
    BreakoutScoreResult,
    MacroRegimeResult,
    Pivot,
    PreBreakoutReadiness,
    ResistanceCluster,
    RetestState,
    calculate_breakout_score,
    calculate_wilder_adx14,
    clamp,
    classify_macro_regime,
    cluster_resistance_pivots,
    evaluate_breakout_gates,
    evaluate_n1_continuation,
    evaluate_pre_breakout_readiness,
    evaluate_retest_candle,
    extract_resistance_pivots,
    is_chronologically_compatible,
    select_primary_resistance_cluster,
)


def make_candle(
    t: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float,
    v_tb: float,
    ct: int = 0,
    n_trades: int = 100,
) -> Dict[str, Any]:
    """Helper to create a standard Binance Spot candle dict with base taker volume."""
    close_time = ct if ct > 0 else (t + 59999)
    return {
        "open_time": t,
        "close_time": close_time,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "taker_buy_base_volume": v_tb,
        "trades": n_trades,
    }


class TestResistanceDetectionAndClustering(unittest.TestCase):
    """Category A: Causal Resistance Detection & Overhead Complete-Linkage Clustering (§ 2)."""

    def test_canonical_pivot_sort_key(self):
        """Canonical 4-level sorting key: (-price, -volume, -timestamp, index)."""
        p1 = Pivot(index=10, timestamp=1000, price=105.0, volume=500.0)
        p2 = Pivot(index=15, timestamp=2000, price=105.0, volume=600.0)
        p3 = Pivot(index=20, timestamp=3000, price=110.0, volume=100.0)
        p4 = Pivot(index=25, timestamp=4000, price=105.0, volume=500.0)

        # Sorted order:
        # Highest price first: p3 (110.0)
        # Next price (105.0): highest volume first -> p2 (600.0)
        # Next volume (500.0): newer timestamp first -> p4 (timestamp 4000)
        # Final tie-breaker: index -> p1 (timestamp 1000)
        pivots = [p1, p2, p3, p4]
        sorted_pivots = sorted(pivots, key=lambda p: p.sort_key)
        self.assertEqual(sorted_pivots, [p3, p2, p4, p1])

    def test_causal_swing_high_extraction_lookback_and_confirmation(self):
        """Swing high requires 3-bar local max and confirmed right-hand candle closed prior to N."""
        # 10 candles: indices 0..9.
        # Candidate breakout candle is index 9 (Bar N).
        # Swing high at index 4 (High 105, neighbors 100).
        # Swing high at index 8 (High 106, neighbors 100).
        # Note: Index 8 has right-hand neighbor at index 9 (Bar N).
        # Since candidate bar is index 9, index 8 CANNOT be confirmed by candle 9!
        # Max confirmed pivot is index <= 7 (since index 7 is confirmed by closed index 8).
        candles = []
        for i in range(10):
            t = 1000 + i * 60000
            if i == 4:
                # Local swing high
                candles.append(make_candle(t, 101.0, 105.0, 99.0, 102.0, 100.0, 50.0))
            elif i == 8:
                # Potential high at N-1
                candles.append(make_candle(t, 102.0, 106.0, 100.0, 103.0, 100.0, 50.0))
            else:
                candles.append(make_candle(t, 100.0, 101.0, 98.0, 100.0, 100.0, 50.0))

        # Evaluate candidate bar index 9
        pivots = extract_resistance_pivots(candles, candidate_bar_index=9, lookback=100)
        # Index 4 must be captured
        self.assertTrue(any(p.index == 4 for p in pivots))
        # Index 8 must NOT be captured because candidate bar N (index 9) cannot be used as confirmation
        self.assertFalse(any(p.index == 8 for p in pivots))

    def test_pairwise_structural_compatibility_rules(self):
        """Pairwise compatibility: tolerance <= 0.35 * ATR, separation >= 4, pullback <= min(P) - 0.50 * ATR."""
        atr_ref = 2.00
        # Condition 1: Tolerance = 0.35 * 2.0 = 0.70
        # Condition 2: Separation = 4 (at least 3 intervening bars)
        # Condition 3: Pullback level = min(100.0, 100.5) - 0.50 * 2.0 = 99.00
        candles = []
        for i in range(15):
            t = 1000 + i * 60000
            low = 98.50 if i == 5 else 99.50  # Bar 5 provides pullback <= 99.00
            high = 100.0 if i == 2 else 100.5 if i == 7 else 99.8
            candles.append(make_candle(t, 99.5, high, low, 99.6, 100.0, 50.0))

        p_a = Pivot(index=2, timestamp=1120000, price=100.0, volume=200.0)
        p_b = Pivot(index=7, timestamp=1420000, price=100.5, volume=250.0)

        # Baseline: compatible
        self.assertTrue(is_chronologically_compatible(p_a, p_b, atr_ref, candles))

        # Failure 1: Price difference exceeds tolerance (> 0.70)
        p_b_wide = Pivot(index=7, timestamp=1420000, price=100.80, volume=250.0)
        self.assertFalse(is_chronologically_compatible(p_a, p_b_wide, atr_ref, candles))

        # Failure 2: Separation < 4 (e.g. index 5, separation = 3)
        p_b_close = Pivot(index=5, timestamp=1300000, price=100.40, volume=250.0)
        self.assertFalse(is_chronologically_compatible(p_a, p_b_close, atr_ref, candles))

        # Failure 3: Insufficient pullback between pivots
        candles_shallow = []
        for i in range(15):
            t = 1000 + i * 60000
            # Low never drops to <= 99.00
            candles_shallow.append(make_candle(t, 99.5, 100.0, 99.40, 99.6, 100.0, 50.0))
        self.assertFalse(is_chronologically_compatible(p_a, p_b, atr_ref, candles_shallow))

    def test_complete_linkage_clustering_and_strictly_overhead(self):
        """Complete-linkage clustering groups compatible pivots and strictly requires R_level > c_prev."""
        atr_ref = 2.00
        c_prev = 99.50  # Current price is 99.50, resistance at ~100.0 is overhead

        candles = []
        for i in range(25):
            t = 1000 + i * 60000
            low = 98.0 if i in (5, 12, 18) else 99.0
            candles.append(make_candle(t, 99.0, 100.2, low, 99.2, 100.0, 50.0))

        p1 = Pivot(index=2, timestamp=1000, price=100.0, volume=100.0)
        p2 = Pivot(index=8, timestamp=2000, price=100.2, volume=120.0)
        p3 = Pivot(index=15, timestamp=3000, price=100.1, volume=110.0)

        # Three compatible pivots at indices 2, 8, 15
        clusters = cluster_resistance_pivots([p1, p2, p3], atr_ref, c_prev, candles)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].touch_count, 3)
        self.assertAlmostEqual(clusters[0].r_level, 100.1, places=4)
        self.assertTrue(clusters[0].r_level > c_prev)

        # Rejection when cluster is NOT overhead (c_prev = 102.0 > R_level 100.1)
        clusters_under = cluster_resistance_pivots([p1, p2, p3], atr_ref, c_prev=102.0, candles=candles)
        self.assertEqual(len(clusters_under), 0)


class TestPreBreakoutReadinessScoring(unittest.TestCase):
    """Category B: Pre-Breakout Readiness Score S_ready (§ 3.2)."""

    def test_s_ready_ideal_inputs(self):
        """Ideal inputs: high accum, tight proximity, tight compression, high DIR."""
        s_accum = 85.0
        r_level = 100.0
        c_prev = 99.80   # dist = 0.20, ATR = 2.0 -> dist / (1.5 * 2.0) = 0.20 / 3.0 = 0.0667 -> S_prox = 93.33
        atr_ref = 2.0
        bbw_prev = 0.02  # S_comp = (1.0 - 0.02 / 0.08) * 100 = 75.0
        dir_1pct = 0.65  # S_dir = clamp((0.65 - 0.50) / 0.20, 0, 1) * 100 = 75.0

        res = evaluate_pre_breakout_readiness(
            s_accum=s_accum,
            r_level=r_level,
            c_prev=c_prev,
            atr_ref=atr_ref,
            bbw_prev=bbw_prev,
            dir_1pct=dir_1pct,
        )
        # Expected:
        # 0.35 * 85.0 = 29.75
        # 0.25 * 93.3333 = 23.3333
        # 0.20 * 75.0 = 15.00
        # 0.20 * 75.0 = 15.00
        # Total = 83.0833
        self.assertAlmostEqual(res.s_ready, 83.08, places=1)
        self.assertTrue(res.s_ready >= 65.0)

    def test_s_ready_unassessed_dir_default(self):
        """Unassessed DIR defaults to 50.0 score."""
        res = evaluate_pre_breakout_readiness(
            s_accum=70.0,
            r_level=100.0,
            c_prev=99.0,
            atr_ref=2.0,
            bbw_prev=0.04,
            dir_1pct=None,
        )
        self.assertEqual(res.s_dir, 50.0)


class TestDailyMacroMarketRegime(unittest.TestCase):
    """Category C: Daily Macro Market Regime via SMA200 & Wilder's ADX14 (§ 3.3, § 13.2)."""

    def test_regime_warmup_when_insufficient_bars(self):
        """Fewer than 200 closed daily BTC bars results in WARMUP (§ 3.3 Stage 1, Example 26)."""
        candles = [make_candle(1000 + i * 86400000, 50000, 51000, 49000, 50500, 1000, 500) for i in range(150)]
        res = classify_macro_regime(candles)
        self.assertEqual(res.regime, "WARMUP")
        self.assertEqual(res.bars_count, 150)

    def test_regime_bear_when_close_below_sma200(self):
        """Close below SMA200 results in BEAR (§ 3.3 Stage 2)."""
        # 200 bars at 60,000, final bar at 55,000 (below SMA200 ~59,975)
        candles = [make_candle(1000 + i * 86400000, 60000, 60500, 59500, 60000, 1000, 500) for i in range(199)]
        candles.append(make_candle(1000 + 199 * 86400000, 56000, 56500, 54500, 55000, 1000, 500))

        res = classify_macro_regime(candles)
        self.assertEqual(res.regime, "BEAR")
        self.assertTrue(res.btc_close < res.sma200)

    def test_example_25_strong_downward_adx_above_sma200(self):
        """Example 25: Price above SMA200, ADX >= 20, but +DI <= -DI results in SIDEWAYS."""
        # Synthesize daily bars where price remains above SMA200, but recent trend is strongly downward
        # Bar closes descend from 70,000 to 65,000 while SMA200 is ~60,000
        candles = []
        for i in range(210):
            t = 1000 + i * 86400000
            if i < 180:
                candles.append(make_candle(t, 59000, 60000, 58000, 59500, 1000, 500))
            elif i < 195:
                candles.append(make_candle(t, 68000, 71000, 67000, 70000, 1000, 500))
            else:
                # Strong downward move (minus_dm dominates)
                step = (i - 195) * 300
                h = 70000 - step
                l = 68000 - step
                c = 68500 - step
                candles.append(make_candle(t, h, h, l, c, 1000, 400))

        res = classify_macro_regime(candles)
        # Price is above SMA200 (~60k)
        self.assertTrue(res.btc_close > res.sma200)
        # ADX trend is downward, so +DI <= -DI => SIDEWAYS
        self.assertEqual(res.regime, "SIDEWAYS")


class TestBreakoutEligibilityGates(unittest.TestCase):
    """Category D: Breakout Eligibility Gates G1-G7 (§ 4.1)."""

    def setUp(self):
        self.r_level = 100.0
        self.atr_ref = 2.00
        # Required clearance: 100.0 + 0.15 * 2.0 = 100.30
        self.candle_n = make_candle(
            t=1000,
            o=99.80,
            h=101.50,
            l=99.50,
            c=101.20,  # > 100.30, close location = (101.20 - 99.50) / 2.00 = 0.85 >= 0.70
            v=2000.0,
            v_tb=1200.0,
        )
        self.rvol_n = 2.20
        self.z_vol_n = 2.50
        self.delta_norm_n = 0.20  # (2 * 1200 - 2000) / 2000 = 0.20 >= 0.15
        self.tbr_n = 0.60         # 1200 / 2000 = 0.60 >= 0.55
        self.prior_state = "PRE_BREAKOUT"
        self.s_accum_prev = 75.0
        self.regime = "BULL"
        self.data_confidence = 90.0

    def test_all_seven_gates_pass(self):
        """All 7 gates pass simultaneously."""
        res = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertTrue(res.passed)
        self.assertEqual(len(res.failed_gates), 0)

    def test_g1_clearance_failure(self):
        """G1 fails when C_N <= R_level + 0.15 * ATR_ref (100.30)."""
        candle_weak = make_candle(1000, 99.8, 100.35, 99.5, 100.25, 2000, 1200)
        res = evaluate_breakout_gates(
            candle_n=candle_weak,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertFalse(res.passed)
        self.assertIn("G1_CLEARANCE", res.failed_gates)

    def test_g2_volume_failure(self):
        """G2 fails when RVOL < 1.75 and Z_vol < 2.0."""
        res = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=1.50,
            z_vol_n=1.80,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertFalse(res.passed)
        self.assertIn("G2_VOLUME", res.failed_gates)

    def test_g3_taker_dominance_failure(self):
        """G3 fails when delta_norm < 0.15 or TBR < 0.55."""
        res = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=0.10,  # Below 0.15
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertFalse(res.passed)
        self.assertIn("G3_TAKER_DOMINANCE", res.failed_gates)

    def test_g4_decisive_close_failure(self):
        """G4 fails when close location < 0.70."""
        # Range = 102.0 - 99.0 = 3.0. Close = 100.5. Close location = 1.5 / 3.0 = 0.50 < 0.70
        candle_mid = make_candle(1000, 99.5, 102.0, 99.0, 100.5, 2000, 1200)
        res = evaluate_breakout_gates(
            candle_n=candle_mid,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertFalse(res.passed)
        self.assertIn("G4_DECISIVE_CLOSE", res.failed_gates)

    def test_g5_prior_state_qualification(self):
        """G5 allows PRE_BREAKOUT, or ACCUMULATION with S_accum >= 65.0; rejects others."""
        # Case 1: ACCUMULATION with S_accum = 60.0 -> fails
        res1 = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state="ACCUMULATION",
            s_accum_prev=60.0,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertFalse(res1.passed)
        self.assertIn("G5_PRIOR_STATE", res1.failed_gates)

        # Case 2: ACCUMULATION with S_accum = 70.0 -> passes
        res2 = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state="ACCUMULATION",
            s_accum_prev=70.0,
            daily_btc_regime=self.regime,
            data_confidence=self.data_confidence,
        )
        self.assertTrue(res2.passed)

    def test_g6_regime_rejection_bear_and_warmup(self):
        """G6 strictly rejects BEAR and WARMUP (§ 4.1 Gate G6)."""
        for bad_regime in ("BEAR", "WARMUP"):
            res = evaluate_breakout_gates(
                candle_n=self.candle_n,
                r_level=self.r_level,
                atr_ref=self.atr_ref,
                rvol_n=self.rvol_n,
                z_vol_n=self.z_vol_n,
                delta_norm_n=self.delta_norm_n,
                tbr_n=self.tbr_n,
                prior_state=self.prior_state,
                s_accum_prev=self.s_accum_prev,
                daily_btc_regime=bad_regime,
                data_confidence=self.data_confidence,
            )
            self.assertFalse(res.passed)
            self.assertIn("G6_REGIME", res.failed_gates)

    def test_g7_data_confidence_threshold(self):
        """G7 fails when data confidence < 70.0."""
        res = evaluate_breakout_gates(
            candle_n=self.candle_n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            rvol_n=self.rvol_n,
            z_vol_n=self.z_vol_n,
            delta_norm_n=self.delta_norm_n,
            tbr_n=self.tbr_n,
            prior_state=self.prior_state,
            s_accum_prev=self.s_accum_prev,
            daily_btc_regime=self.regime,
            data_confidence=68.0,
        )
        self.assertFalse(res.passed)
        self.assertIn("G7_DATA_CONFIDENCE", res.failed_gates)


class TestBreakoutConvictionScoring(unittest.TestCase):
    """Category E: Breakout Conviction Scoring S_break (§ 4.2)."""

    def test_s_break_weighting_and_continuation_eligibility(self):
        """Strong breakout in BULL regime achieves S_break >= 80.0 -> direct continuation eligible."""
        candle_n = make_candle(1000, 100.0, 101.50, 99.80, 101.40, 3500.0, 2800.0)
        r_level = 100.0
        atr_ref = 2.00
        # Clearance: C_N = 101.40. R + 0.10 * ATR = 100.20. Dist = 1.20. 0.30 * ATR = 0.60. clamp(1.20/0.60) = 1.0 -> S_clear = 100.0
        # Volume: RVOL = 3.50 -> (3.50 - 1.75) / 1.75 = 1.0 -> S_vol = 100.0
        # Delta: Delta_norm = 0.45 -> clamp((0.45 - 0.15) / 0.30) = 1.0 -> S_delta = 100.0
        # Shape: Range = 1.70. Body = 1.40 / 1.70 = 0.8235 -> clamp = 0.92437. Close = 1.60 / 1.70 = 0.9412 -> clamp = 1.0 -> S_shape = 96.22
        # S_context = 85.0
        res = calculate_breakout_score(
            candle_n=candle_n,
            r_level=r_level,
            atr_ref=atr_ref,
            rvol_n=3.50,
            z_vol_n=3.00,
            delta_norm_n=0.45,
            s_context=85.0,
            regime="BULL",
        )
        # Expected score: 0.25*100 + 0.25*100 + 0.20*100 + 0.15*96.2185 + 0.15*85 = 97.18
        self.assertAlmostEqual(res.s_break, 97.18, places=2)
        self.assertTrue(res.is_eligible)
        self.assertTrue(res.continuation_eligible)
        self.assertFalse(res.retest_mandatory)

    def test_s_break_sideways_forces_retest_mandatory(self):
        """Even with S_break >= 80.0, SIDEWAYS regime mandates support retest (§ 4.2)."""
        candle_n = make_candle(1000, 100.0, 101.50, 99.80, 101.40, 3500.0, 2800.0)
        res = calculate_breakout_score(
            candle_n=candle_n,
            r_level=100.0,
            atr_ref=2.00,
            rvol_n=3.50,
            z_vol_n=3.00,
            delta_norm_n=0.45,
            s_context=85.0,
            regime="SIDEWAYS",
        )
        self.assertTrue(res.is_eligible)
        self.assertFalse(res.continuation_eligible)
        self.assertTrue(res.retest_mandatory)


class TestBarN1ContinuationDecisionHierarchy(unittest.TestCase):
    """Category F: Bar N+1 Continuation Decision Hierarchy (§ 7, Examples 7-10)."""

    def setUp(self):
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0
        self.candle_n = make_candle(1000, 100.0, 102.0, 99.8, 102.0, 2000.0, 1300.0)

    def test_example_07_n1_wick_breach(self):
        """Example 7: N+1 Low breaches wick limit (100.0 - 0.25 * 2.0 = 99.50) -> Priority 3 STRUCTURE_INVALIDATED."""
        # Low = 99.40 < 99.50
        candle_n1 = make_candle(2000, 102.0, 102.5, 99.40, 101.0, 900.0, 500.0)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.20,
        )
        self.assertEqual(status, "STRUCTURE_INVALIDATED")
        self.assertTrue(details["p_wick_breach"])

    def test_example_08_n1_continuation_confirmed(self):
        """Example 8: S_break >= 80, BULL, C_{N+1} > C_N, Low >= Mid_break, Delta_norm > 0 -> CONTINUATION_CONFIRMED."""
        # Mid_break = (100.0 + 102.0) / 2.0 = 101.00
        # Candle N+1: Open 102.0, High 104.0, Low 101.50 (>= 101.0), Close 103.50 (> 102.0), Delta_norm = +0.25
        candle_n1 = make_candle(2000, 102.0, 104.0, 101.50, 103.50, 1500.0, 937.5)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.25,
        )
        self.assertEqual(status, "CONTINUATION_CONFIRMED")
        self.assertTrue(details["p_cont_price"])
        self.assertTrue(details["p_cont_flow"])

    def test_example_09_n1_retest_pullback(self):
        """Example 9: Retest pullback into zone (Low <= 100.40, Close >= 99.80, V <= 1.05 * baseline) -> RETEST_PENDING."""
        # RetestZoneHigh = 100.0 + 0.20 * 2.0 = 100.40
        # Candle N+1: High 102.0, Low 100.20 (<= 100.40), Close 100.50 (>= 99.80), V = 1000 <= 1050
        candle_n1 = make_candle(2000, 102.0, 102.0, 100.20, 100.50, 1000.0, 500.0)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=75.0,  # Retest mandatory path
            regime="SIDEWAYS",
            delta_norm_n1=-0.05,
        )
        self.assertEqual(status, "RETEST_PENDING")
        self.assertTrue(details["p_retest_touch"])
        self.assertTrue(details["p_retest_vol"])

    def test_priority_06_retest_high_volume_rejection(self):
        """Priority 6: Retest touch with heavy volume (V > 1.05 * baseline) -> STRUCTURE_INVALIDATED."""
        # Low = 100.20 (touch), but V = 1500 > 1050
        candle_n1 = make_candle(2000, 102.0, 102.0, 100.20, 100.50, 1500.0, 600.0)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=75.0,
            regime="BULL",
            delta_norm_n1=0.05,
        )
        self.assertEqual(status, "STRUCTURE_INVALIDATED")
        self.assertTrue(details["p_retest_touch"])
        self.assertFalse(details["p_retest_vol"])

    def test_example_10_n1_stalled_fallback(self):
        """Example 10: Did not continue, did not reach retest zone -> Priority 7 STALLED_PENDING."""
        # Candle N+1: High 102.50, Low 100.80 (> 100.40 zone high), Close 101.50 (<= C_N 102.0)
        candle_n1 = make_candle(2000, 102.0, 102.50, 100.80, 101.50, 900.0, 450.0)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.10,
        )
        self.assertEqual(status, "STALLED_PENDING")


class TestMultiCandleRetestLifecycle(unittest.TestCase):
    """Category G: Multi-Candle Retest Lifecycle & Bounce Confirmation (§ 8, Example 11)."""

    def setUp(self):
        self.n = 10  # Breakout bar index N = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_example_11_retest_bounce_on_n6(self):
        """Example 11: Valid touch on N+4 (bar 14), stalls on N+5 (bar 15), confirmed on N+6 (bar 16)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )

        all_bars = []
        # Bar 11 (N+1): Stalled
        b11 = make_candle(1000, 102.0, 102.5, 101.0, 101.5, 800.0, 400.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "PENDING")
        self.assertEqual(retest_state.touch_count, 0)

        # Bar 12 (N+2): Stalled
        b12 = make_candle(2000, 101.5, 102.0, 100.8, 101.0, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "PENDING")

        # Bar 13 (N+3): Stalled
        b13 = make_candle(3000, 101.0, 101.5, 100.6, 100.8, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "PENDING")

        # Bar 14 (N+4): Touch registered (Low = 100.20 <= 100.40)
        # High = 101.20. Bounce window set to min(14 + 2, 16) = 16.
        b14 = make_candle(4000, 100.8, 101.2, 100.20, 100.5, 900.0, 450.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.bounce_window_end, 16)

        # Bar 15 (N+5): Stalls inside window (High = 101.0, Close = 100.7)
        b15 = make_candle(5000, 100.5, 101.0, 100.3, 100.7, 850.0, 425.0)
        all_bars.append(b15)
        retest_state = evaluate_retest_candle(retest_state, b15, bar_index=15, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 16 (N+6): Dips to 100.15, closes at 102.50 > RefHigh (101.20) with Delta_norm = +0.20
        b16 = make_candle(6000, 100.7, 102.6, 100.15, 102.50, 1200.0, 720.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=0.20, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_CONFIRMED")
        self.assertAlmostEqual(retest_state.ref_high, 101.20, places=2)

    def test_retest_expiry_when_bounce_window_exceeded(self):
        """Retest expires when bar exceeds bounce_window_end without confirmation (§ 8.1 Step 4)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )

        all_bars = []
        # Bar 11 (N+1): Touch registered (Low = 100.20 <= 100.40) -> bounce_window_end = min(11+2, 16) = 13
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")
        self.assertEqual(retest_state.bounce_window_end, 13)

        # Bar 12 (N+2): Stalls outside touch zone (Low = 100.60 > 100.40)
        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 13 (N+3): Final bar of bounce window (13). Closes at 100.9 <= RefHigh (101.5)
        b13 = make_candle(3000, 100.7, 101.2, 100.55, 100.9, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 14 (N+4): Exceeds bounce window (bar 14 > 13) without confirmation -> EXPIRED
        b14 = make_candle(4000, 100.9, 101.1, 100.60, 100.8, 800.0, 400.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "EXPIRED")

    def test_retest_invalidation_on_volume_expansion(self):
        """Retest invalidates if volume exceeds 1.25 * baseline during active touch (§ 8.1 Step 1)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )

        all_bars = []
        # Bar 11 (N+1): Touch with heavy selling volume (V = 1300 > 1250) -> STRUCTURE_INVALIDATED
        b11 = make_candle(1000, 101.0, 101.2, 100.1, 100.2, 1300.0, 400.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=-0.38, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("exceeded 1.25 * baseline", retest_state.invalidation_reason)




# ===========================================================================
# Stage 1 Corrective Verification Tests (Retest Lifecycle Integrity, Idempotency & Coverage)
# Authoritative Baseline: SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md
# ===========================================================================
import copy
import json
import math


class TestRetestLifecycleIntegrityAndExpiry(unittest.TestCase):
    """Category H: Retest Lifecycle Integrity, Bounce Window Expiry & Precedence (§ 8, Correction A)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_bounce_on_exact_final_permitted_bar(self):
        """Bounce on exact final permitted bar of bounce window confirms normally (§ 8.1 Step 3)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11 (N+1): Touch registered (Low = 100.20 <= 100.40) -> bounce_window_end = min(11+2, 16) = 13
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")
        self.assertEqual(retest_state.bounce_window_end, 13)

        # Bar 12 (N+2): Stalls outside touch zone (Low = 100.60 > 100.40, High = 101.0, Close = 100.7)
        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 13 (N+3): Exact final permitted bar of bounce window (13).
        # RefHigh from bars [11, 12] is max(101.5, 101.0) = 101.5.
        # Bar 13 closes at 101.8 > 101.5 with positive delta -> RETEST_CONFIRMED
        b13 = make_candle(3000, 100.7, 102.0, 100.60, 101.8, 1100.0, 700.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.25, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_CONFIRMED")
        self.assertEqual(retest_state.current_bar_index, 13)

    def test_bounce_one_bar_after_permitted_window_rejected(self):
        """Bounce attempted one bar after bounce_window_end must NOT confirm; state expires (§ Correction A)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11 (N+1): Touch registered -> bounce_window_end = 13
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 12 (N+2): Stalls
        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 13 (N+3): Stalls without bounce (Close 100.9 <= RefHigh 101.5)
        b13 = make_candle(3000, 100.7, 101.2, 100.55, 100.9, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 14 (N+4): Closes above RefHigh with positive delta, BUT bar 14 > bounce_window_end (13).
        # Must be EXPIRED and NOT confirm!
        b14 = make_candle(4000, 101.0, 103.0, 100.80, 102.5, 1200.0, 800.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.40, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "EXPIRED")
        self.assertIn("Exceeded bounce window", retest_state.invalidation_reason)

    def test_bounce_after_n_plus_6_rejected(self):
        """Any candle arriving after N+6 must be EXPIRED and never confirmed (§ Correction A)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bars 11-15 stalled
        for idx in range(11, 16):
            b = make_candle(idx * 1000, 101.0, 101.5, 100.6, 100.8, 800.0, 400.0)
            all_bars.append(b)
            retest_state = evaluate_retest_candle(retest_state, b, bar_index=idx, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 16 (N+6): Touch registered (Low = 100.20 <= 100.40)
        b16 = make_candle(16000, 100.8, 101.2, 100.20, 100.4, 800.0, 400.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=0.0, all_retest_candles=all_bars)
        # Bar 16 closes without confirmation -> hard expires at N+6
        self.assertEqual(retest_state.status, "EXPIRED")

        # Bar 17 (N+7): Arrives with strong bounce attempt
        b17 = make_candle(17000, 100.5, 103.0, 100.5, 102.8, 1200.0, 800.0)
        all_bars.append(b17)
        retest_state = evaluate_retest_candle(retest_state, b17, bar_index=17, delta_norm_t=0.45, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "EXPIRED")

    def test_bounce_before_expiry_confirms_normally(self):
        """Bounce occurring strictly within active bounce window confirms normally (§ 8.1 Step 3)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11 (N+1): Touch registered
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.5, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 12 (N+2): Bounces above RefHigh (101.5) on very next bar
        b12 = make_candle(2000, 100.5, 102.5, 100.4, 102.0, 1100.0, 750.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.30, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_CONFIRMED")
        self.assertEqual(retest_state.ref_high, 101.5)

    def test_structural_invalidation_on_final_permitted_candle_precedence(self):
        """Structural invalidation on final permitted candle retains strict priority over expiry (§ Correction A)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11 (N+1): Touch registered -> bounce_window_end = 13
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 12 (N+2): Stalls
        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 13 (N+3): Final permitted bar of bounce window, but Low breaches wick limit (99.40 < 99.50)
        b13 = make_candle(3000, 100.7, 101.0, 99.40, 99.9, 900.0, 450.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("breached wick limit", retest_state.invalidation_reason)


class TestRetestStateSerializationAndRestoration(unittest.TestCase):
    """Category I: Complete RetestState Serialization, Restoration & Validation (§ 8, Correction B)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_full_state_round_trip_with_active_bounce_window(self):
        """Full RetestState round-trip via JSON and seamless resumed evaluation (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Serialize -> JSON -> Deserialization
        serialized = retest_state.to_dict()
        json_str = json.dumps(serialized)
        deserialized_dict = json.loads(json_str)
        restored_state = RetestState.from_dict(deserialized_dict)

        self.assertEqual(restored_state.breakout_bar_index, retest_state.breakout_bar_index)
        self.assertEqual(restored_state.r_level, retest_state.r_level)
        self.assertEqual(restored_state.atr_ref, retest_state.atr_ref)
        self.assertEqual(restored_state.vol_baseline, retest_state.vol_baseline)
        self.assertEqual(restored_state.current_bar_index, retest_state.current_bar_index)
        self.assertEqual(restored_state.touch_count, retest_state.touch_count)
        self.assertEqual(restored_state.lowest_retest_price, retest_state.lowest_retest_price)
        self.assertEqual(restored_state.bounce_window_end, retest_state.bounce_window_end)
        self.assertEqual(restored_state.touch_bars, retest_state.touch_bars)
        self.assertEqual(restored_state.status, retest_state.status)
        self.assertEqual(restored_state.last_processed_bar_index, retest_state.last_processed_bar_index)

        # Continue evaluation on both original and restored states with Bar 12
        b12 = make_candle(2000, 100.6, 102.5, 100.5, 102.0, 1100.0, 750.0)
        all_bars.append(b12)
        state_original = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.35, all_retest_candles=all_bars)
        state_resumed = evaluate_retest_candle(restored_state, b12, bar_index=12, delta_norm_t=0.35, all_retest_candles=all_bars)

        self.assertEqual(state_original.to_dict(), state_resumed.to_dict())
        self.assertEqual(state_resumed.status, "RETEST_CONFIRMED")

    def test_full_state_round_trip_with_multiple_touches(self):
        """RetestState with multiple touches round-trips and resumes identically (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.5, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        b12 = make_candle(2000, 100.5, 101.0, 100.10, 100.4, 950.0, 475.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 2)
        self.assertEqual(retest_state.touch_bars, [11, 12])

        # Serialize & restore
        restored = RetestState.from_dict(json.loads(json.dumps(retest_state.to_dict())))
        self.assertEqual(restored.touch_count, 2)
        self.assertEqual(restored.touch_bars, [11, 12])
        self.assertEqual(restored.lowest_retest_price, 100.10)
        self.assertEqual(restored.bounce_window_end, 14)

    def test_missing_vol_baseline_rejected(self):
        """from_dict strictly rejects missing vol_baseline (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        d = retest_state.to_dict()
        del d["vol_baseline"]
        with self.assertRaises(ValueError) as ctx:
            RetestState.from_dict(d)
        self.assertIn("Missing required field: 'vol_baseline'", str(ctx.exception))

    def test_missing_required_fields_rejected(self):
        """from_dict strictly rejects missing any required state field (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        for req in ["breakout_bar_index", "r_level", "atr_ref", "current_bar_index", "touch_count", "status"]:
            d = retest_state.to_dict()
            del d[req]
            with self.assertRaises(ValueError):
                RetestState.from_dict(d)

    def test_invalid_numeric_values_rejected(self):
        """from_dict strictly rejects NaN, Inf, non-positive, and invalid numeric values (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        invalid_cases = [
            ("r_level", float("nan")),
            ("r_level", float("inf")),
            ("r_level", -10.0),
            ("r_level", 0.0),
            ("atr_ref", float("nan")),
            ("atr_ref", -1.0),
            ("atr_ref", 0.0),
            ("vol_baseline", float("nan")),
            ("vol_baseline", -100.0),
            ("vol_baseline", 0.0),
            ("touch_count", -1),
            ("breakout_bar_index", -1),
            ("current_bar_index", 5),  # current_bar_index < breakout_bar_index
        ]
        for field_name, bad_val in invalid_cases:
            d = retest_state.to_dict()
            d[field_name] = bad_val
            with self.assertRaises(ValueError):
                RetestState.from_dict(d)

    def test_unordered_touch_bars_rejected(self):
        """from_dict strictly rejects unordered touch_bars (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=14,
            touch_count=2,
            lowest_retest_price=100.20,
            bounce_window_end=15,
            touch_bars=[13, 11],  # unordered!
        )
        with self.assertRaises(ValueError):
            RetestState.from_dict(retest_state.to_dict())

    def test_inconsistent_touch_history_rejected(self):
        """from_dict strictly rejects inconsistent touch history (§ Correction B)."""
        d = {
            "breakout_bar_index": 10,
            "r_level": 100.0,
            "atr_ref": 2.0,
            "vol_baseline": 1000.0,
            "current_bar_index": 12,
            "touch_count": 2,  # Claims 2 touches, but touch_bars has only 1
            "lowest_retest_price": 100.20,
            "bounce_window_end": 13,
            "touch_bars": [11],
            "ref_high": None,
            "status": "RETEST_PENDING",
            "invalidation_reason": None,
            "last_processed_bar_index": 12,
        }
        with self.assertRaises(ValueError):
            RetestState.from_dict(d)

    def test_terminal_state_restoration_does_not_reopen(self):
        """Terminal states remain terminal and immutable after serialization and restore (§ Correction B)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=12,
            touch_count=1,
            lowest_retest_price=99.20,
            bounce_window_end=13,
            touch_bars=[11],
            status="STRUCTURE_INVALIDATED",
            invalidation_reason="Low 99.20 breached wick limit",
            last_processed_bar_index=12,
        )
        restored = RetestState.from_dict(retest_state.to_dict())
        self.assertEqual(restored.status, "STRUCTURE_INVALIDATED")

        # Evaluate another candle on terminal restored state
        b13 = make_candle(3000, 100.5, 103.0, 100.5, 102.8, 1200.0, 800.0)
        res = evaluate_retest_candle(restored, b13, bar_index=13, delta_norm_t=0.40, all_retest_candles=[b13])
        self.assertEqual(res.status, "STRUCTURE_INVALIDATED")
        self.assertEqual(res.invalidation_reason, "Low 99.20 breached wick limit")


class TestDuplicateCandleProcessingAndIdempotency(unittest.TestCase):
    """Category J: Duplicate Candle Processing and Chronological Idempotency (§ 8, Correction C)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_duplicate_candle_does_not_mutate_state(self):
        """Submitting the same candle twice must not increment touch_count or duplicate touch_bars (§ Correction C)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.touch_bars, [11])
        self.assertEqual(retest_state.bounce_window_end, 13)
        state_snapshot = copy.deepcopy(retest_state.to_dict())

        # Submit identical bar 11 again
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.to_dict(), state_snapshot)
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.touch_bars, [11])
        self.assertEqual(retest_state.bounce_window_end, 13)

    def test_out_of_order_bar_rejected_without_state_mutation(self):
        """Submitting an earlier bar after a later bar must be safely ignored without mutation (§ Correction C)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)

        state_after_12 = copy.deepcopy(retest_state.to_dict())

        # Now submit bar 10 (N) or bar 11 (out of order)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.to_dict(), state_after_12)

    def test_idempotency_survives_serialization_and_restore(self):
        """Idempotency protection survives serialization and deserialization (§ Correction C)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Serialize and restore
        restored = RetestState.from_dict(retest_state.to_dict())
        snapshot = copy.deepcopy(restored.to_dict())

        # Resubmit bar 11 to restored state
        restored = evaluate_retest_candle(restored, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(restored.to_dict(), snapshot)


class TestTouchCountSemanticsAndBounceWindow(unittest.TestCase):
    """Category K: Touch Semantics and Bounce Window Deadline Handling (§ 8.1, Correction D)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_one_isolated_touch(self):
        """Single isolated touch increments count to 1 and sets bounce_window_end (§ 8.1 Step 2)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=[b11])
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.bounce_window_end, 13)

    def test_two_consecutive_touch_candles(self):
        """Two consecutive qualifying candles count as 2 diagnostic touches and advance deadline (§ Correction D)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.bounce_window_end, 13)

        b12 = make_candle(2000, 100.6, 101.0, 100.30, 100.7, 900.0, 450.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 2)
        self.assertEqual(retest_state.touch_bars, [11, 12])
        # bounce_window_end is advanced to min(12 + 2, 16) = 14
        self.assertEqual(retest_state.bounce_window_end, 14)

    def test_touch_separation_and_subsequent_new_touch(self):
        """A touch, stall separation, and subsequent touch within active window resets deadline (§ Correction D)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11: Touch (Low = 100.20)
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.bounce_window_end, 13)

        # Bar 12: Separation (Low = 100.70 > 100.40)
        b12 = make_candle(2000, 100.75, 101.2, 100.70, 100.9, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.bounce_window_end, 13)

        # Bar 13: New touch within active window (Low = 100.15)
        b13 = make_candle(3000, 100.8, 101.0, 100.15, 100.5, 950.0, 475.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 2)
        self.assertEqual(retest_state.bounce_window_end, min(13 + 2, 16))  # 15
        self.assertEqual(retest_state.touch_bars, [11, 13])

    def test_repeated_touches_near_n6_clamped_at_n6(self):
        """Repeated touches cannot extend bounce_window_end past N+6 (§ 8.1 Step 2, Correction D)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        for idx in range(11, 15):
            b = make_candle(idx * 1000, 101.0, 101.5, 100.6, 100.8, 800.0, 400.0)
            all_bars.append(b)
            retest_state = evaluate_retest_candle(retest_state, b, bar_index=idx, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 15 (N+5): Touch -> min(15 + 2, 16) = 16
        b15 = make_candle(15000, 100.8, 101.0, 100.20, 100.5, 900.0, 450.0)
        all_bars.append(b15)
        retest_state = evaluate_retest_candle(retest_state, b15, bar_index=15, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.bounce_window_end, 16)

        # Bar 16 (N+6): Touch again -> min(16 + 2, 16) = 16
        b16 = make_candle(16000, 100.5, 101.0, 100.20, 100.4, 900.0, 450.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.bounce_window_end, 16)
        # Without confirmation, hard-expires at N+6
        self.assertEqual(retest_state.status, "EXPIRED")


class TestClosedCandleAndChronologicalIntegrity(unittest.TestCase):
    """Category L: Closed-Candle Contract and Chronological Validation (§ 8, Correction E)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_forming_historical_candle_excluded_from_ref_high(self):
        """Forming candle failing as_of_ms check must NOT contribute to ref_high (§ Correction E)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11: Touch registered
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0, ct=60999)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars, as_of_ms=1000000)

        # Bar 12: Historical bar is forming relative to as_of_ms = 70000 (close_time 120999 >= 70000)
        b12_forming = make_candle(61000, 100.6, 103.0, 100.5, 102.0, 900.0, 450.0, ct=120999)
        all_bars.append(b12_forming)

        # Bar 13: Attempts to bounce, evaluated with as_of_ms = 70000
        b13 = make_candle(121000, 102.0, 103.5, 101.8, 103.0, 1200.0, 800.0, ct=180999)
        all_bars.append(b13)

        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.30, all_retest_candles=all_bars, as_of_ms=70000)
        # Because bar 12 was forming as of 70000, it cannot contribute to ref_high, failing confirmation safely
        self.assertNotEqual(retest_state.status, "RETEST_CONFIRMED")

    def test_closed_historical_candle_contributes_normally(self):
        """Fully closed historical candles contribute to ref_high normally (§ Correction E)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0, ct=60999)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars, as_of_ms=500000)

        b12 = make_candle(61000, 100.6, 102.0, 100.5, 101.2, 900.0, 450.0, ct=120999)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars, as_of_ms=500000)

        # Bar 13: Closes at 102.5 > ref_high (102.0) with as_of_ms = 500000
        b13 = make_candle(121000, 101.2, 102.8, 101.0, 102.5, 1200.0, 800.0, ct=180999)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.35, all_retest_candles=all_bars, as_of_ms=500000)
        self.assertEqual(retest_state.status, "RETEST_CONFIRMED")
        self.assertEqual(retest_state.ref_high, 102.0)

    def test_corrupt_historical_data_fails_safely(self):
        """Corrupt historical candle data fails safely and does not produce false confirmation (§ Correction E)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Corrupt candle in history (negative price)
        b12_corrupt = {"open_time": 2000, "close_time": 20059, "open": -10.0, "high": 10.0, "low": 5.0, "close": 8.0, "volume": 100.0, "taker_buy_base_volume": 50.0}
        all_bars.append(b12_corrupt)

        b13 = make_candle(3000, 101.0, 103.0, 101.0, 102.8, 1200.0, 800.0)
        all_bars.append(b13)

        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.35, all_retest_candles=all_bars)
        self.assertNotEqual(retest_state.status, "RETEST_CONFIRMED")

    def test_candle_outside_n1_n6_rejected(self):
        """Candles outside [N+1, N+6] are rejected (§ Correction E)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        # Bar 10 (N) submitted: out-of-order prior to retest window
        b10 = make_candle(500, 100.0, 102.0, 99.8, 101.9, 1000.0, 500.0)
        state_res = evaluate_retest_candle(retest_state, b10, bar_index=10, delta_norm_t=0.0, all_retest_candles=[b10])
        self.assertEqual(state_res.current_bar_index, self.n)
        self.assertEqual(state_res.status, "PENDING")


class TestN1DecisionValidationAndHierarchy(unittest.TestCase):
    """Category M: Bar N+1 Decision Validation and Hierarchy Coverage (§ 7, Correction F)."""

    def setUp(self):
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0
        self.candle_n = make_candle(1000, 100.0, 102.0, 99.8, 102.0, 2000.0, 1300.0, ct=1059)
        self.candle_n1 = make_candle(2000, 102.0, 103.0, 101.5, 102.5, 1000.0, 600.0, ct=2059)

    def test_priority_01_corrupt_candle_rejected(self):
        """Priority 1: Corrupt or None candle must yield DATA_INVALID (§ Correction F)."""
        status, details = evaluate_n1_continuation(
            candle_n=None,
            candle_n1=self.candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.20,
        )
        self.assertEqual(status, "DATA_INVALID")

        status2, _ = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1={"open_time": 2000},  # missing fields
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.20,
        )
        self.assertEqual(status2, "DATA_INVALID")

    def test_priority_01_non_chronological_candles(self):
        """Priority 1: Non-chronological candles must yield DATA_INVALID (§ Correction F)."""
        candle_n1_inverted = make_candle(500, 102.0, 103.0, 101.5, 102.5, 1000.0, 600.0, ct=559)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,  # open 1000
            candle_n1=candle_n1_inverted,  # open 500
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.20,
        )
        self.assertEqual(status, "DATA_INVALID")
        self.assertIn("Non-chronological", details["rejection_reason"])

    def test_priority_01_invalid_numeric_inputs_rejected(self):
        """Priority 1: NaN, infinite, non-positive parameters and invalid regime yield DATA_INVALID (§ Correction F)."""
        bad_inputs = [
            {"r_level": float("nan")},
            {"r_level": -100.0},
            {"atr_ref": float("nan")},
            {"atr_ref": 0.0},
            {"vol_baseline": -10.0},
            {"s_break": float("nan")},
            {"delta_norm_n1": float("inf")},
            {"regime": "SUPER_BULL"},
        ]
        for bad in bad_inputs:
            kwargs = {
                "candle_n": self.candle_n,
                "candle_n1": self.candle_n1,
                "r_level": self.r_level,
                "atr_ref": self.atr_ref,
                "vol_baseline": self.vol_baseline,
                "s_break": 85.0,
                "regime": "BULL",
                "delta_norm_n1": 0.20,
            }
            kwargs.update(bad)
            status, _ = evaluate_n1_continuation(**kwargs)
            self.assertEqual(status, "DATA_INVALID", f"Failed to reject invalid input: {bad}")

    def test_priority_02_floor_breach_close_takes_precedence(self):
        """Priority 2: Close breaching floor limit takes precedence over wick breach and touches (§ 7.2)."""
        # floor_limit = 100.0 - 0.10 * 2.0 = 99.80.
        # wick_limit = 100.0 - 0.25 * 2.0 = 99.50.
        # Here Close = 99.70 (< 99.80 floor breach), but Low = 99.60 (>= 99.50, NO wick breach).
        candle_n1 = make_candle(2000, 102.0, 102.5, 99.60, 99.70, 900.0, 450.0, ct=2059)
        status, details = evaluate_n1_continuation(
            candle_n=self.candle_n,
            candle_n1=candle_n1,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            s_break=85.0,
            regime="BULL",
            delta_norm_n1=0.20,
        )
        self.assertEqual(status, "STRUCTURE_INVALIDATED")
        self.assertTrue(details["p_floor_breach"])
        self.assertFalse(details["p_wick_breach"])


class TestResistanceClusteringTieBreakers(unittest.TestCase):
    """Category N: Primary Resistance Cluster Selection & Deterministic Tie-Breaker (§ 2.4)."""

    def test_select_primary_resistance_cluster_nearest_overhead(self):
        """Nearest overhead resistance cluster is selected (§ 2.4)."""
        p1 = Pivot(index=1, timestamp=1000, price=102.0, volume=100.0)
        p2 = Pivot(index=5, timestamp=5000, price=102.1, volume=100.0)
        c1 = ResistanceCluster(members=[p1, p2])

        p3 = Pivot(index=2, timestamp=2000, price=105.0, volume=100.0)
        p4 = Pivot(index=6, timestamp=6000, price=105.1, volume=100.0)
        c2 = ResistanceCluster(members=[p3, p4])

        selected = select_primary_resistance_cluster([c2, c1], c_prev=100.0)
        self.assertIsNotNone(selected)
        self.assertEqual(selected, c1)

    def test_select_primary_resistance_cluster_tie_breaker_touch_count(self):
        """At equal distance, cluster with highest touch count wins tie-breaker (§ 2.4)."""
        # Cluster A: 2 touches at price 103.0
        pa1 = Pivot(index=1, timestamp=1000, price=103.0, volume=100.0)
        pa2 = Pivot(index=5, timestamp=5000, price=103.0, volume=100.0)
        cA = ResistanceCluster(members=[pa1, pa2])

        # Cluster B: 3 touches at price 103.0
        pb1 = Pivot(index=2, timestamp=2000, price=103.0, volume=100.0)
        pb2 = Pivot(index=6, timestamp=6000, price=103.0, volume=100.0)
        pb3 = Pivot(index=9, timestamp=9000, price=103.0, volume=100.0)
        cB = ResistanceCluster(members=[pb1, pb2, pb3])

        selected = select_primary_resistance_cluster([cA, cB], c_prev=100.0)
        self.assertEqual(selected, cB)

    def test_select_primary_resistance_cluster_tie_breaker_span(self):
        """At equal distance and touch count, cluster with lowest span wins tie-breaker (§ 2.4)."""
        # Cluster A: 2 touches, span = 0.40 (102.8 to 103.2, mean 103.0)
        pa1 = Pivot(index=1, timestamp=1000, price=102.8, volume=100.0)
        pa2 = Pivot(index=5, timestamp=5000, price=103.2, volume=100.0)
        cA = ResistanceCluster(members=[pa1, pa2])

        # Cluster B: 2 touches, span = 0.10 (102.95 to 103.05, mean 103.0)
        pb1 = Pivot(index=2, timestamp=2000, price=102.95, volume=100.0)
        pb2 = Pivot(index=6, timestamp=6000, price=103.05, volume=100.0)
        cB = ResistanceCluster(members=[pb1, pb2])

        selected = select_primary_resistance_cluster([cA, cB], c_prev=100.0)
        self.assertEqual(selected, cB)


class TestWilderADX14IndependentReference(unittest.TestCase):
    """Category O: Wilder ADX14 Compared Against Independent First-Principles Reference Calculation (§ 3.3, § 13.2)."""

    @staticmethod
    def _reference_wilder_adx14(highs: List[float], lows: List[float], closes: List[float], period: int = 14):
        """Textbook reference implementation of J. Welles Wilder Jr. (1978) ADX14 from first principles.

        Step-by-step:
        1. TR_t = max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)
        2. +DM_t = (H_t - H_{t-1}) if (H_t - H_{t-1} > L_{t-1} - L_t and H_t - H_{t-1} > 0) else 0.0
        3. -DM_t = (L_{t-1} - L_t) if (L_{t-1} - L_t > H_t - H_{t-1} and L_{t-1} - L_t > 0) else 0.0
        4. Initial 14-period sum: S_TR = sum(TR[1:15]), S_+DM = sum(+DM[1:15]), S_-DM = sum(-DM[1:15])
           Initial smoothed = S / 14
        5. Running Wilder smoothing: S_t = S_{t-1} - (S_{t-1} / 14) + X_t = (S_{t-1} * 13 + X_t) / 14
        6. +DI_t = (Smooth_+DM / Smooth_TR) * 100, -DI_t = (Smooth_-DM / Smooth_TR) * 100
        7. DX_t = (| +DI - -DI | / (+DI + -DI)) * 100
        8. Initial ADX (bar 28) = arithmetic mean of first 14 DX values (DX_1 through DX_14)
        9. Subsequent ADX: ADX_t = (ADX_{t-1} * 13 + DX_t) / 14
        """
        n = len(closes)
        if n < 2 * period + 1:
            return None, None, None

        tr_vals = []
        p_dm_vals = []
        m_dm_vals = []
        for i in range(1, n):
            h, l, prev_c = highs[i], lows[i], closes[i - 1]
            prev_h, prev_l = highs[i - 1], lows[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            up = h - prev_h
            down = prev_l - l
            p_dm = up if (up > down and up > 0.0) else 0.0
            m_dm = down if (down > up and down > 0.0) else 0.0
            tr_vals.append(tr)
            p_dm_vals.append(p_dm)
            m_dm_vals.append(m_dm)

        # Initial smoothing (mean of first 14 bars)
        smooth_tr = sum(tr_vals[:period]) / period
        smooth_p_dm = sum(p_dm_vals[:period]) / period
        smooth_m_dm = sum(m_dm_vals[:period]) / period

        dx_series = []
        p_di = (smooth_p_dm / smooth_tr * 100.0) if smooth_tr > 1e-12 else 0.0
        m_di = (smooth_m_dm / smooth_tr * 100.0) if smooth_tr > 1e-12 else 0.0
        dx_series.append(abs(p_di - m_di) / (p_di + m_di) * 100.0 if (p_di + m_di) > 1e-12 else 0.0)

        for i in range(period, len(tr_vals)):
            smooth_tr = (smooth_tr * (period - 1) + tr_vals[i]) / period
            smooth_p_dm = (smooth_p_dm * (period - 1) + p_dm_vals[i]) / period
            smooth_m_dm = (smooth_m_dm * (period - 1) + m_dm_vals[i]) / period
            p_di = (smooth_p_dm / smooth_tr * 100.0) if smooth_tr > 1e-12 else 0.0
            m_di = (smooth_m_dm / smooth_tr * 100.0) if smooth_tr > 1e-12 else 0.0
            dx = abs(p_di - m_di) / (p_di + m_di) * 100.0 if (p_di + m_di) > 1e-12 else 0.0
            dx_series.append(dx)

        adx = sum(dx_series[:period]) / period
        for dx_val in dx_series[period:]:
            adx = (adx * (period - 1) + dx_val) / period

        return adx, p_di, m_di

    def test_wilder_adx14_against_independent_reference_series(self):
        """calculate_wilder_adx14 matches independent textbook reference across continuous 50-bar series (§ 13.2)."""
        # Create 50 synthetic trending price bars
        highs = []
        lows = []
        closes = []
        base = 100.0
        for i in range(50):
            # Upward trending series with volatility
            c = base + i * 1.5 + (0.5 if i % 2 == 0 else -0.5)
            h = c + 1.2
            l = c - 1.1
            highs.append(h)
            lows.append(l)
            closes.append(c)

        adx_impl, p_di_impl, m_di_impl = calculate_wilder_adx14(highs, lows, closes, period=14)
        adx_ref, p_di_ref, m_di_ref = self._reference_wilder_adx14(highs, lows, closes, period=14)

        self.assertIsNotNone(adx_impl)
        self.assertIsNotNone(adx_ref)
        self.assertAlmostEqual(adx_impl, adx_ref, places=6)
        self.assertAlmostEqual(p_di_impl, p_di_ref, places=6)
        self.assertAlmostEqual(m_di_impl, m_di_ref, places=6)


if __name__ == "__main__":
    unittest.main()
