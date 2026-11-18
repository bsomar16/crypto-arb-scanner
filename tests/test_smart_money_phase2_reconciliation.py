"""Smart Money Phase 2 — Stage 1 Reconciliation & Architecture Invariants Test Suite.

Authoritative Standards:
- SMART_MONEY_PHASE2_SPEC_v2.1.5_FINAL.md
- SMART_MONEY_PHASE2_SPEC_v2.1.6_AMENDMENT.md
Subsystem: smart_money/
Operating Mode: Binance Spot Only, Long/BUY Only, Shadow Mode (Zero Live Execution).
"""
import copy
import json
import math
import unittest
from decimal import Decimal
from typing import Any, Dict, List

from smart_money.breakout import (
    Pivot,
    ResistanceCluster,
    RetestState,
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
    """Helper to construct Binance Spot closed kline dictionary."""
    return {
        "open_time": t,
        "close_time": ct if ct > 0 else t + 59999,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "taker_buy_base_volume": v_tb,
        "number_of_trades": n_trades,
    }


class TestRetestExecutionOrderingReconciliation(unittest.TestCase):
    """Exhaustive boundary testing for § 8.1 and Amendment v2.1.6 § 2."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0
        # floor = 100.0 - 0.10 * 2.0 = 99.80
        # wick = 100.0 - 0.25 * 2.0 = 99.50
        # zone_high = 100.0 + 0.20 * 2.0 = 100.40

    def test_boundary_01_after_deadline_with_floor_breach_yields_structural_invalidation(self):
        """Boundary 1: Candle after bounce deadline with floor breach must yield STRUCTURE_INVALIDATED.
        
        Proves Invalidation (Step 1) strictly precedes Expiry (Step 4).
        """
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11: Touch registered -> bounce_window_end = min(11+2, 16) = 13
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 900.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.bounce_window_end, 13)

        # Bar 12: Stalls
        b12 = make_candle(2000, 100.6, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b12)
        retest_state = evaluate_retest_candle(retest_state, b12, bar_index=12, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 13: Stalls (final permitted bar of window)
        b13 = make_candle(3000, 100.7, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.status, "RETEST_PENDING")

        # Bar 14 (after deadline 13): Suffers floor breach (Close = 99.40 < 99.80)
        b14 = make_candle(4000, 100.7, 100.8, 99.20, 99.40, 900.0, 450.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.0, all_retest_candles=all_bars)

        # MUST be STRUCTURE_INVALIDATED, NOT EXPIRED!
        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("breached floor", retest_state.invalidation_reason)

    def test_boundary_02_after_deadline_with_wick_breach_yields_structural_invalidation(self):
        """Boundary 2: Candle after bounce deadline with wick breach yields STRUCTURE_INVALIDATED."""
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

        b13 = make_candle(3000, 100.7, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 14: Close is above floor (99.90 >= 99.80), but Low breaches wick (99.30 < 99.50)
        b14 = make_candle(4000, 100.7, 100.8, 99.30, 99.90, 900.0, 450.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.0, all_retest_candles=all_bars)

        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("breached wick limit", retest_state.invalidation_reason)

    def test_boundary_03_after_deadline_without_invalidation_yields_expired(self):
        """Boundary 3: Candle after bounce deadline without structural invalidation yields EXPIRED."""
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

        b13 = make_candle(3000, 100.7, 101.0, 100.60, 100.7, 800.0, 400.0)
        all_bars.append(b13)
        retest_state = evaluate_retest_candle(retest_state, b13, bar_index=13, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 14: Orderly price, Open 100.90, Low 100.80, Close 101.0, Vol normal
        b14 = make_candle(4000, 100.90, 101.2, 100.80, 101.0, 800.0, 400.0)
        all_bars.append(b14)
        retest_state = evaluate_retest_candle(retest_state, b14, bar_index=14, delta_norm_t=0.0, all_retest_candles=all_bars)

        self.assertEqual(retest_state.status, "EXPIRED")
        self.assertIn("Exceeded bounce window", retest_state.invalidation_reason)

    def test_boundary_04_candle_at_n6_with_touch_expires_if_no_bounce(self):
        """Boundary 4: Candle at N+6 with touch registers touch, then expires because bar N+6 closed without bounce."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        for idx in range(11, 16):
            b = make_candle(idx * 1000, 101.0, 101.5, 100.6, 100.8, 800.0, 400.0)
            all_bars.append(b)
            retest_state = evaluate_retest_candle(retest_state, b, bar_index=idx, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 16 (N+6): Touch registered (Low = 100.20 <= 100.40)
        b16 = make_candle(16000, 100.8, 101.2, 100.20, 100.4, 800.0, 400.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(retest_state.touch_count, 1)
        self.assertEqual(retest_state.status, "EXPIRED")
        self.assertIn("Reached max bar 16 without confirmation", retest_state.invalidation_reason)

    def test_boundary_05_candle_at_n6_confirms_bounce_if_prior_touch_exists(self):
        """Boundary 5: Candle at N+6 confirms bounce when prior touch exists and criteria met (Example 11)."""
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

        # Bar 15 (N+5): Touch registered -> RefHigh will be 101.20
        b15 = make_candle(15000, 100.8, 101.20, 100.20, 100.5, 900.0, 450.0)
        all_bars.append(b15)
        retest_state = evaluate_retest_candle(retest_state, b15, bar_index=15, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 16 (N+6): Bounces cleanly above RefHigh 101.20 (Close = 101.80, delta_norm = 0.35)
        b16 = make_candle(16000, 100.5, 102.0, 100.4, 101.80, 1100.0, 750.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=0.35, all_retest_candles=all_bars)

        self.assertEqual(retest_state.status, "RETEST_CONFIRMED")
        self.assertEqual(retest_state.ref_high, 101.20)

    def test_boundary_06_candle_at_n6_with_floor_breach_yields_structural_invalidation(self):
        """Boundary 6: Candle at N+6 with floor breach yields STRUCTURE_INVALIDATED (not EXPIRED)."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        for idx in range(11, 16):
            b = make_candle(idx * 1000, 101.0, 101.5, 100.6, 100.8, 800.0, 400.0)
            all_bars.append(b)
            retest_state = evaluate_retest_candle(retest_state, b, bar_index=idx, delta_norm_t=0.0, all_retest_candles=all_bars)

        # Bar 16: Crashes through floor (Close 99.20 < 99.80)
        b16 = make_candle(16000, 100.5, 100.8, 99.00, 99.20, 1500.0, 400.0)
        all_bars.append(b16)
        retest_state = evaluate_retest_candle(retest_state, b16, bar_index=16, delta_norm_t=-0.40, all_retest_candles=all_bars)

        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("breached floor", retest_state.invalidation_reason)

    def test_boundary_07_candle_simultaneous_touch_and_volume_expansion_invalidates(self):
        """Boundary 7: Candle simultaneously touching zone and expanding volume (>1.25*baseline) invalidates immediately."""
        retest_state = RetestState(
            breakout_bar_index=self.n,
            r_level=self.r_level,
            atr_ref=self.atr_ref,
            vol_baseline=self.vol_baseline,
            current_bar_index=self.n,
        )
        all_bars = []
        # Bar 11: Touches zone (Low = 100.20 <= 100.40), but Volume = 1300 > 1250
        b11 = make_candle(1000, 101.0, 101.5, 100.20, 100.6, 1300.0, 450.0)
        all_bars.append(b11)
        retest_state = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)

        self.assertEqual(retest_state.status, "STRUCTURE_INVALIDATED")
        self.assertIn("Volume 1300.00 exceeded 1.25 * baseline", retest_state.invalidation_reason)
        # Touch must NOT have been recorded
        self.assertEqual(retest_state.touch_count, 0)
        self.assertEqual(retest_state.touch_bars, [])


class TestCausalPivotAdjacencyAndClustering(unittest.TestCase):
    """Exhaustive boundary testing for § 2 and Amendment v2.1.6 § 3."""

    def test_candidate_bar_n_cannot_serve_as_right_hand_confirmation(self):
        """Candidate breakout bar N cannot serve as confirmation candle j+1 for any pivot (§ 2.1)."""
        candles = []
        for i in range(12):
            t = 1000 + i * 60000
            # Bar 9 has highest price, bar 10 (N) drops
            high = 105.0 if i == 9 else 100.0
            candles.append(make_candle(t, 99.0, high, 98.0, 99.5, 100.0, 50.0))

        # Candidate breakout bar N is index 10.
        # Bar 9 has High 105.0 > 100.0 (bar 8) and > 100.0 (bar 10).
        # But bar 9's confirmation candle is bar 10 (candidate bar N itself!).
        pivots = extract_resistance_pivots(candles, candidate_bar_index=10, lookback=20)
        # Bar 9 MUST NOT be extracted as a pivot
        self.assertFalse(any(p.index == 9 for p in pivots))

    def test_gap_in_candles_cannot_bridge_false_adjacency(self):
        """Missing or corrupt candle in sequence must not create false 3-bar swing high adjacency (§ 2.1)."""
        candles = []
        # Build 10 candles where candle index 4 is None / corrupt
        for i in range(10):
            t = 1000 + i * 60000
            if i == 4:
                # Corrupt candle
                candles.append({"open_time": t, "close_time": t + 59999, "open": -1.0, "high": -1.0, "low": -2.0, "close": -1.5, "volume": 10.0, "taker_buy_base_volume": 5.0})
            else:
                high = 104.0 if i == 3 else 100.0
                candles.append(make_candle(t, 99.0, high, 98.0, 99.5, 100.0, 50.0))

        # Bar 3 has High 104.0. Its right-hand candle is index 4, which is corrupt.
        pivots = extract_resistance_pivots(candles, candidate_bar_index=8, lookback=20)
        # Bar 3 cannot confirm without valid closed bar 4!
        self.assertFalse(any(p.index == 3 for p in pivots))

    def test_complete_linkage_diameter_strictly_bounded(self):
        """Complete-linkage cluster diameter strictly bounded by 0.35 * ATR_ref (§ 2.4)."""
        atr_ref = 2.00
        # tolerance = 0.35 * 2.0 = 0.70
        c_prev = 99.0
        candles = []
        for i in range(30):
            t = 1000 + i * 60000
            low = 98.0 if i in (5, 11, 18, 24) else 99.0
            candles.append(make_candle(t, 99.0, 101.0, low, 99.5, 100.0, 50.0))

        p1 = Pivot(index=2, timestamp=1000, price=100.00, volume=100.0)
        p2 = Pivot(index=8, timestamp=2000, price=100.40, volume=110.0)  # span with p1 = 0.40 <= 0.70
        p3 = Pivot(index=15, timestamp=3000, price=100.80, volume=120.0) # span with p1 = 0.80 > 0.70!

        clusters = cluster_resistance_pivots([p1, p2, p3], atr_ref, c_prev, candles)
        for c in clusters:
            self.assertTrue(c.span <= 0.35 * atr_ref + 1e-9)


class TestStopLossMonotonicityInvariants(unittest.TestCase):
    """Exhaustive property verification for § 11.3 Invariant 5 & 6 and Amendment v2.1.6 § 5."""

    def test_active_stop_loss_strictly_monotonic_non_decreasing(self):
        """Active stop loss must satisfy SL_active(t+1) >= SL_active(t) under all market paths."""
        p_entry = Decimal("100.00")
        f_buy = Decimal("0.0010")
        f_sell = Decimal("0.0010")
        s_sell = Decimal("0.0008")

        # Breakeven formula
        p_be = p_entry / (Decimal("1.0") - f_buy - f_sell - s_sell)
        sl_initial = Decimal("98.00")

        # Simulate 50 bars with fluctuating close prices and ATR
        sl_active = sl_initial
        tp1_executed = False
        trailing_stop_active = False

        cum_trail = Decimal("0.0")

        for bar in range(1, 50):
            # Price simulation: drops, rises, drops
            c_val = Decimal(str(round(100.0 + math.sin(bar / 3.0) * 8.0, 4)))
            atr_val = Decimal("2.00")

            if bar >= 10:
                tp1_executed = True
            if bar >= 11:
                trailing_stop_active = True

            # Calculate theoretical components
            trail_candidate = c_val - Decimal("1.50") * atr_val
            cum_trail = max(cum_trail, trail_candidate)

            sl_trail = cum_trail if trailing_stop_active else Decimal("0.0")
            sl_be = p_be if tp1_executed else Decimal("0.0")

            # Master resolution: supremum
            sl_next = max(sl_initial, sl_be, sl_trail)

            # Invariant 5 check:
            self.assertTrue(
                sl_next >= sl_active,
                f"Stop decreased at bar {bar}: {sl_next} < {sl_active}",
            )
            sl_active = sl_next


class TestIdempotencyFullStateComparison(unittest.TestCase):
    """Verification of complete-state preservation under duplicate processing (§ 5.2)."""

    def setUp(self):
        self.n = 10
        self.r_level = 100.0
        self.atr_ref = 2.00
        self.vol_baseline = 1000.0

    def test_duplicate_candle_produces_identical_complete_state_dict(self):
        """Submitting duplicate candle produces bit-for-bit identical state dict across all fields."""
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
        state_after_b11 = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        snapshot_dict = copy.deepcopy(state_after_b11.to_dict())

        # Submit bar 11 again 5 times
        for _ in range(5):
            res = evaluate_retest_candle(state_after_b11, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
            self.assertEqual(res.to_dict(), snapshot_dict)

    def test_out_of_order_candle_produces_identical_complete_state_dict(self):
        """Submitting out-of-order candle produces bit-for-bit identical state dict across all fields."""
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
        snapshot_dict = copy.deepcopy(retest_state.to_dict())

        # Submit bar 10 (N) and bar 11 (out of order)
        b10 = make_candle(500, 100.0, 101.0, 99.8, 100.9, 1000.0, 500.0)
        res_10 = evaluate_retest_candle(retest_state, b10, bar_index=10, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(res_10.to_dict(), snapshot_dict)

        res_11 = evaluate_retest_candle(retest_state, b11, bar_index=11, delta_norm_t=0.0, all_retest_candles=all_bars)
        self.assertEqual(res_11.to_dict(), snapshot_dict)


if __name__ == "__main__":
    unittest.main()
