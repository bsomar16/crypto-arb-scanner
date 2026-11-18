"""Phase 1.1 Focused Unit Test Suite for Smart Money Subsystem.

Tests:
1. Binance Kline Data Integrity (validations, rejection of q_tb, bounds, OHLC consistency)
2. Closed-Candle Contract Enforcement (as_of_ms validation)
3. Volume Delta & Cumulative Volume Delta (CVD) continuous calculation
4. OLS linear regression CVD slope (positive, negative, flat/zero variance)
5. Multi-condition Selling Pressure Absorption & Negative Counter-Tests
6. Hard Invariant: Positive CVD alone is NEVER absorption
7. Structural Price/CVD Bullish Divergence (paired swing lows, false divergence tests)
8. Volume Dry-Up Integrity (all-up, all-down, insufficient observations, confirmed dry-up)
9. Higher-Low Context Gating (sideways base vs runaway rising trend vs descending vs volatile chop)
10. Truthful Classification Reasons (never claim absorption without binary confirmation)
11. Scoring Weights Validation (unknown keys, negative weights, zero sum, normalization)
12. Order Book Depth Imbalance (DIR 1% & 2%, exact boundaries, crossed books, invalid levels)
13. Edge Cases & Isolation Non-Regression
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from smart_money import (
    DEFAULT_ACCUMULATION_WEIGHTS,
    AccumulationResult,
    VolumeCvdResult,
    calculate_orderbook_dir,
    compute_volume_cvd_analytics,
    cvd_linear_regression_slope,
    evaluate_accumulation_structure,
    validate_accumulation_weights,
)


def _make_candle(
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
    """Helper to construct synthetic closed Binance kline dict."""
    d = {
        "t": t,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
        "v_tb": v_tb,
        "n_trades": n_trades,
    }
    if ct > 0:
        d["ct"] = ct
    return d


class TestKlineDataIntegrity(unittest.TestCase):
    """Test suite for Binance Spot kline data integrity and validation."""

    def test_rejection_of_q_tb_without_base_taker_volume(self):
        """Rule: q_tb (quote volume) is NOT base volume. Missing v_tb must be rejected."""
        candle_with_qtb_only = {
            "t": 1000, "o": 10.0, "h": 10.5, "l": 9.8, "c": 10.2, "v": 100.0,
            "q_tb": 600.0,  # quote volume only, missing v_tb
        }
        res = compute_volume_cvd_analytics([candle_with_qtb_only] * 10)
        self.assertFalse(res.data_quality_flags["taker_volume_present"])
        self.assertGreater(res.data_quality_flags["rejected_candles_count"], 0)

    def test_taker_volume_bounds_validation(self):
        """Taker buy volume must satisfy: 0 <= v_tb <= v."""
        # Case A: Negative taker volume
        bad_negative = [_make_candle(1000 + i * 60_000, 10.0, 10.5, 9.8, 10.2, 100.0, -5.0) for i in range(10)]
        res_a = compute_volume_cvd_analytics(bad_negative)
        self.assertEqual(res_a.data_quality_flags["rejected_candles_count"], 10)

        # Case B: Taker volume exceeds total volume
        bad_excess = [_make_candle(1000 + i * 60_000, 10.0, 10.5, 9.8, 10.2, 100.0, 150.0) for i in range(10)]
        res_b = compute_volume_cvd_analytics(bad_excess)
        self.assertEqual(res_b.data_quality_flags["rejected_candles_count"], 10)

    def test_ohlcv_consistency_validation(self):
        """Invalid OHLC relationships must be rejected and penalize data confidence."""
        # High less than open
        bad_h = [_make_candle(1000 + i * 60_000, 10.0, 9.5, 9.0, 9.8, 100.0, 50.0) for i in range(10)]
        res_h = compute_volume_cvd_analytics(bad_h)
        self.assertFalse(res_h.data_quality_flags["valid_ohlcv"])

        # Low greater than close
        bad_l = [_make_candle(1000 + i * 60_000, 10.0, 11.0, 10.5, 10.2, 100.0, 50.0) for i in range(10)]
        res_l = compute_volume_cvd_analytics(bad_l)
        self.assertFalse(res_l.data_quality_flags["valid_ohlcv"])

        # Non-positive price
        bad_p = [_make_candle(1000 + i * 60_000, 0.0, 10.0, 0.0, 5.0, 100.0, 50.0) for i in range(10)]
        res_p = compute_volume_cvd_analytics(bad_p)
        self.assertFalse(res_p.data_quality_flags["valid_ohlcv"])

        # Negative volume
        bad_v = [_make_candle(1000 + i * 60_000, 10.0, 11.0, 9.0, 10.0, -100.0, 0.0) for i in range(10)]
        res_v = compute_volume_cvd_analytics(bad_v)
        self.assertFalse(res_v.data_quality_flags["valid_ohlcv"])

    def test_duplicate_and_non_monotonic_timestamps(self):
        """Duplicate timestamps must be flagged and penalize data confidence."""
        candles = [
            _make_candle(1000 + i * 1000, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0)
            for i in range(10)
        ]
        candles[4]["t"] = candles[3]["t"]  # duplicate timestamp
        res = compute_volume_cvd_analytics(candles)
        self.assertFalse(res.data_quality_flags["timestamps_strictly_increasing"])
        self.assertLessEqual(res.data_confidence, 70.0)

    def test_closed_candle_enforcement_with_as_of_ms(self):
        """If as_of_ms is provided, unclosed/active candles must be strictly excluded."""
        now_eval = 10_000
        candles = [
            _make_candle(1000 + i * 1000, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0, ct=1999 + i * 1000)
            for i in range(9)  # 9 closed candles (ct from 1999 to 9999 < 10000)
        ]
        # 10th candle is unclosed (ct = 10999 >= 10000)
        candles.append(_make_candle(10000, 10.8, 11.2, 10.6, 11.0, 100.0, 50.0, ct=10999))
        res = compute_volume_cvd_analytics(candles, as_of_ms=now_eval)
        self.assertTrue(res.data_quality_flags["closed_candles_verified"])
        self.assertEqual(res.data_quality_flags["rejected_candles_count"], 1)
        self.assertEqual(len(res.cvd_series), 9)

    def test_as_of_ms_exact_boundary_rejection(self):
        """Boundary test: close_time == as_of_ms must be REJECTED as unclosed."""
        now_eval = 5000
        candles = [_make_candle(1000 + i * 500, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0, ct=1499 + i * 500) for i in range(6)]
        # Candle with ct == now_eval exactly (5000)
        candles.append(_make_candle(4500, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0, ct=5000))
        res = compute_volume_cvd_analytics(candles, as_of_ms=now_eval)
        self.assertEqual(res.data_quality_flags["rejected_candles_count"], 1)
        self.assertEqual(len(res.cvd_series), 6)

    def test_as_of_ms_prior_millisecond_accepted(self):
        """Boundary test: close_time == as_of_ms - 1 must be ACCEPTED as closed."""
        now_eval = 5000
        candles = [_make_candle(1000 + i * 500, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0, ct=1499 + i * 500) for i in range(6)]
        # Candle with ct == 4999 (as_of_ms - 1)
        candles.append(_make_candle(4500, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0, ct=4999))
        res = compute_volume_cvd_analytics(candles, as_of_ms=now_eval)
        self.assertEqual(res.data_quality_flags["rejected_candles_count"], 0)
        self.assertEqual(len(res.cvd_series), 7)

    def test_out_of_order_timestamps_handled_consistently(self):
        """Out-of-order timestamps must be flagged and penalize data confidence."""
        candles = [
            _make_candle(1000, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0),
            _make_candle(3000, 10.2, 10.6, 10.0, 10.4, 100.0, 50.0),
            _make_candle(2000, 10.4, 10.7, 10.1, 10.5, 100.0, 50.0),  # out-of-order (2000 < 3000)
            _make_candle(4000, 10.5, 10.8, 10.2, 10.6, 100.0, 50.0),
            _make_candle(5000, 10.6, 10.9, 10.3, 10.7, 100.0, 50.0),
            _make_candle(6000, 10.7, 11.0, 10.4, 10.8, 100.0, 50.0),
        ]
        res = compute_volume_cvd_analytics(candles)
        self.assertFalse(res.data_quality_flags["timestamps_strictly_increasing"])
        self.assertLessEqual(res.data_confidence, 70.0)


class TestVolumeCvdAnalytics(unittest.TestCase):
    """Test suite for Volume Intelligence and CVD calculations."""

    def test_delta_and_cvd_chronological_continuity(self):
        """Volume delta = 2 * v_tb - v. CVD must be strictly cumulative."""
        candles = [
            _make_candle(1000, 10.0, 10.5, 9.8, 10.2, 100.0, 60.0),
            _make_candle(2000, 10.2, 10.4, 9.9, 10.0, 200.0, 80.0),
            _make_candle(3000, 10.0, 10.3, 9.7, 10.1, 150.0, 75.0),
            _make_candle(4000, 10.1, 10.8, 10.0, 10.7, 300.0, 200.0),
            _make_candle(5000, 10.7, 10.9, 10.4, 10.5, 100.0, 30.0),
        ]
        res = compute_volume_cvd_analytics(candles, lookback=10, slope_window=5)
        self.assertAlmostEqual(res.delta, -40.0, places=4)
        self.assertAlmostEqual(res.cvd_latest, 40.0, places=4)
        expected_cvd = [20.0, -20.0, -20.0, 80.0, 40.0]
        self.assertEqual(len(res.cvd_series), 5)
        for actual, exp in zip(res.cvd_series, expected_cvd):
            self.assertAlmostEqual(actual, exp, places=4)

    def test_ols_cvd_slope(self):
        """OLS linear regression slope must accurately reflect trend and handle zero variance."""
        series_up = [5.0, 7.0, 9.0, 11.0, 13.0]
        raw_slope, norm_slope = cvd_linear_regression_slope(series_up, norm_baseline=10.0)
        self.assertAlmostEqual(raw_slope, 2.0, places=4)
        self.assertAlmostEqual(norm_slope, 0.2, places=4)

        series_down = [20.0, 17.0, 14.0, 11.0, 8.0]
        raw_slope_dn, _ = cvd_linear_regression_slope(series_down, norm_baseline=1.0)
        self.assertAlmostEqual(raw_slope_dn, -3.0, places=4)

        series_flat = [10.0, 10.0, 10.0, 10.0]
        raw_slope_flat, _ = cvd_linear_regression_slope(series_flat)
        self.assertAlmostEqual(raw_slope_flat, 0.0, places=4)

    def test_multi_condition_absorption_positive_case(self):
        """Absorption requires: heavy selling, resilient close/wick, support held, elevated volume."""
        candles = []
        base_t = 100_000
        for i in range(30):
            candles.append(_make_candle(base_t + i * 60_000, 100.0, 101.0, 99.5, 100.2, v=100.0, v_tb=50.0))
        candles.append(_make_candle(base_t + 30 * 60_000, 100.0, 100.8, 99.6, 100.5, v=300.0, v_tb=70.0))

        res = compute_volume_cvd_analytics(candles, lookback=30)
        self.assertTrue(res.absorption_detected)
        self.assertGreater(len(res.absorption_reasons), 0)

    def test_positive_cvd_alone_never_classified_as_absorption(self):
        """RULE: Positive delta alone must NEVER be classified as absorption."""
        candles = [_make_candle(200_000 + i * 60_000, 50.0, 51.0, 49.5, 50.5, v=100.0, v_tb=50.0) for i in range(30)]
        candles.append(_make_candle(200_000 + 30 * 60_000, 50.5, 53.0, 50.4, 52.8, v=500.0, v_tb=450.0))
        res = compute_volume_cvd_analytics(candles)
        self.assertFalse(res.absorption_detected)

    def test_absorption_negative_cases(self):
        """Counter-tests where 1 or more absorption conditions fail."""
        base_t = 300_000
        # Broken support
        candles_breakdown = [_make_candle(base_t + i * 60_000, 100.0, 101.0, 99.5, 100.0, 100.0, 50.0) for i in range(30)]
        candles_breakdown.append(_make_candle(base_t + 30 * 60_000, 99.5, 99.6, 95.0, 95.2, v=400.0, v_tb=60.0))
        res_a = compute_volume_cvd_analytics(candles_breakdown)
        self.assertFalse(res_a.absorption_detected)

        # Low volume (no effort)
        candles_low_vol = [_make_candle(base_t + i * 60_000, 100.0, 101.0, 99.5, 100.0, 100.0, 50.0) for i in range(30)]
        candles_low_vol.append(_make_candle(base_t + 30 * 60_000, 100.0, 100.5, 99.6, 100.4, v=40.0, v_tb=10.0))
        res_b = compute_volume_cvd_analytics(candles_low_vol)
        self.assertFalse(res_b.absorption_detected)


class TestPriceCvdDivergence(unittest.TestCase):
    """Test suite for structural paired swing low price/CVD bullish divergence."""

    def test_confirmed_paired_swing_low_divergence(self):
        """Price makes lower low across distinct swing troughs with intermediate bounce, but CVD is higher."""
        candles = []
        base_t = 400_000
        # 25 bars:
        # Bars 0-5: descend to Trough 1 at p=100.0 (low=99.5, bar 5), delta=0, cvd=0
        for i in range(6):
            p = 105.0 - i * 1.0
            candles.append(_make_candle(base_t + i * 60_000, p + 0.5, p + 0.8, p - 0.5, p, 100.0, 50.0))
        # Bars 6-12: bounce to 103.0 (peak at bar 9), delta=+40 each bar, cvd rises to +280
        for i in range(6, 13):
            p = 100.0 + (i - 5) * 0.5
            candles.append(_make_candle(base_t + i * 60_000, p - 0.3, p + 0.6, p - 0.4, p, 100.0, 70.0))
        # Bars 13-18: pull back to Trough 2 at p=99.0 (low=98.8, lower than Trough 1)
        # But aggressive buyers step in: v_tb = 85 (delta=+70 each bar, cvd rises to +700)
        for i in range(13, 19):
            p = 103.5 - (i - 12) * 0.75
            candles.append(_make_candle(base_t + i * 60_000, p + 0.4, p + 0.6, p - 0.5, p, 100.0, 85.0))

        res = compute_volume_cvd_analytics(candles, divergence_lookback=25)
        self.assertTrue(res.cvd_bullish_divergence)
        self.assertGreater(res.divergence_bars, 0)

    def test_false_divergence_when_cvd_also_makes_lower_low(self):
        """If price makes a lower low AND CVD also makes a lower low, divergence must be FALSE."""
        candles = []
        base_t = 500_000
        # Aggressive selling throughout: delta = -60 each bar
        for i in range(20):
            p = 100.0 - i * 0.5
            candles.append(_make_candle(base_t + i * 60_000, p + 0.2, p + 0.4, p - 0.4, p, 100.0, 20.0))
        res = compute_volume_cvd_analytics(candles, divergence_lookback=20)
        self.assertFalse(res.cvd_bullish_divergence)

    def test_divergence_insufficient_history(self):
        """Fewer than 10 bars cannot confirm a paired swing divergence."""
        candles = [_make_candle(1000 + i * 60_000, 10.0, 10.5, 9.8, 10.2, 100.0, 50.0) for i in range(8)]
        res = compute_volume_cvd_analytics(candles)
        self.assertFalse(res.cvd_bullish_divergence)


class TestVolumeDryUpIntegrity(unittest.TestCase):
    """Test suite ensuring volume dry-up is not triggered on insufficient observations."""

    def test_all_up_candles_does_not_trigger_dryup(self):
        """Rule: A series of all-up candles must NOT falsely report volume dry-up."""
        candles = [
            _make_candle(1000 + i * 60_000, 10.0 + i * 0.1, 10.5 + i * 0.1, 9.9 + i * 0.1, 10.3 + i * 0.1, 100.0, 60.0)
            for i in range(35)
        ]
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertFalse(accum.volume_dryup, "All up candles must not trigger volume dry-up")
        self.assertEqual(accum.factor_scores["volume_dryup"], 0.0)
        self.assertFalse(accum.factor_availability["volume_dryup"])

    def test_all_down_candles_does_not_trigger_dryup(self):
        """Rule: A series of all-down candles must NOT report volume dry-up."""
        candles = [
            _make_candle(1000 + i * 60_000, 15.0 - i * 0.1, 15.2 - i * 0.1, 14.5 - i * 0.1, 14.7 - i * 0.1, 100.0, 40.0)
            for i in range(35)
        ]
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertFalse(accum.volume_dryup)
        self.assertEqual(accum.factor_scores["volume_dryup"], 0.0)
        self.assertFalse(accum.factor_availability["volume_dryup"])

    def test_insufficient_down_candles_unassessed(self):
        """Only 1 or 2 down candles must be marked unassessed (minimum 3 required)."""
        candles = []
        for i in range(30):
            # 28 up candles, 2 down candles
            if i in (10, 20):
                c = _make_candle(1000 + i * 60_000, 10.2, 10.4, 9.9, 10.0, 50.0, 20.0)  # down bar
            else:
                c = _make_candle(1000 + i * 60_000, 10.0, 10.5, 9.9, 10.3, 100.0, 60.0)  # up bar
            candles.append(c)
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertFalse(accum.volume_dryup)
        self.assertEqual(accum.factor_scores["volume_dryup"], 0.0)
        self.assertFalse(accum.factor_availability["volume_dryup"])

    def test_confirmed_volume_dryup_with_sufficient_samples(self):
        """When at least 3 down bars exist and down volume is light, dry-up is confirmed."""
        candles = []
        for i in range(30):
            if i % 3 == 0:
                # 10 down bars with very low volume (30.0)
                candles.append(_make_candle(1000 + i * 60_000, 10.2, 10.3, 9.9, 10.0, 30.0, 10.0))
            else:
                # 20 up bars with elevated volume (150.0)
                candles.append(_make_candle(1000 + i * 60_000, 10.0, 10.5, 9.9, 10.4, 150.0, 100.0))
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertTrue(accum.volume_dryup)
        self.assertEqual(accum.factor_scores["volume_dryup"], 1.0)
        self.assertTrue(accum.factor_availability["volume_dryup"])


class TestHigherLowContext(unittest.TestCase):
    """Test suite ensuring continuous trends and wide channels do not qualify as accumulation."""

    def test_sideways_base_with_higher_lows_qualifies(self):
        """Tight base (bandwidth <= 14%) with ascending pivot lows qualifies for accumulation."""
        candles = []
        for i in range(35):
            low_val = 100.0 + (i / 35.0) * 1.5  # 100.0 -> 101.5 (bandwidth ~3.5%)
            high_val = low_val + 2.0
            o_val = low_val + 0.5
            c_val = low_val + 1.5
            v = 150.0 if (i % 2 == 0) else 80.0
            candles.append(_make_candle(1000 + i * 60_000, o_val, high_val, low_val, c_val, v, v * 0.6))
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertTrue(accum.higher_lows)
        self.assertEqual(accum.factor_scores["higher_lows"], 1.0)
        self.assertLess(accum.bandwidth_pct, 14.0)

    def test_runaway_rising_trend_disqualified_from_accumulation(self):
        """Continuous rising trend with wide bandwidth (>22%) must NOT be classified as accumulation."""
        candles = []
        for i in range(40):
            # Price runs from 100 to 140 (bandwidth 40%)
            p = 100.0 + i * 1.0
            candles.append(_make_candle(1000 + i * 60_000, p - 0.5, p + 1.0, p - 1.0, p + 0.5, 1000.0, 600.0))
        accum = evaluate_accumulation_structure(candles, lookback=40)
        self.assertEqual(accum.factor_scores["higher_lows"], 0.0)
        self.assertEqual(accum.classification, "NONE")

    def test_descending_trend_has_no_higher_lows(self):
        """Descending trend with lower lows must have higher_lows = False."""
        candles = []
        for i in range(35):
            p = 100.0 - i * 0.8
            candles.append(_make_candle(1000 + i * 60_000, p + 0.5, p + 0.8, p - 0.8, p - 0.3, 100.0, 45.0))
        accum = evaluate_accumulation_structure(candles, lookback=30)
        self.assertFalse(accum.higher_lows)
        self.assertEqual(accum.factor_scores["higher_lows"], 0.0)
        self.assertEqual(accum.classification, "NONE")


class TestScoringAndClassificationReasons(unittest.TestCase):
    """Test suite for truthful reasons and weight validation."""

    def test_never_claim_absorption_without_binary_detector(self):
        """Classification reason must NEVER claim absorption if absorption_detected is False."""
        candles = []
        for i in range(35):
            low_val = 100.0 + (i / 35.0) * 1.5
            candles.append(_make_candle(1000 + i * 60_000, low_val + 0.5, low_val + 2.0, low_val, low_val + 1.2, 100.0, 55.0))
        accum = evaluate_accumulation_structure(candles, lookback=30)
        for reason in accum.reasons:
            if "absorption" in reason.lower():
                self.fail(f"Reason falsely mentioned absorption: {reason}")

    def test_weights_validation_rejects_invalid_inputs(self):
        """Unknown keys, negative weights, and zero sum must raise ValueError."""
        # Unknown key
        with self.assertRaises(ValueError):
            validate_accumulation_weights({"non_existent_factor": 10.0})

        # Negative weight
        with self.assertRaises(ValueError):
            validate_accumulation_weights({"base_structure": -5.0})

        # Non-numeric weight
        with self.assertRaises(ValueError):
            validate_accumulation_weights({"base_structure": "heavy"})  # type: ignore

        # Total sum <= 0
        with self.assertRaises(ValueError):
            validate_accumulation_weights({k: 0.0 for k in DEFAULT_ACCUMULATION_WEIGHTS})

    def test_valid_custom_weights_merge(self):
        """Valid custom weights merge and normalize correctly."""
        w = validate_accumulation_weights({"base_structure": 50.0, "higher_lows": 50.0})
        self.assertEqual(w["base_structure"], 50.0)
        self.assertEqual(w["higher_lows"], 50.0)
        self.assertEqual(w["absorption"], DEFAULT_ACCUMULATION_WEIGHTS["absorption"])


class TestAbsorptionScoringStrictness(unittest.TestCase):
    """Test suite ensuring absorption factor score is strictly binary based on confirmed absorption."""

    def test_absorption_factor_strictly_one_when_absorption_detected(self):
        """When absorption_detected is True, absorption factor score must be strictly 1.0."""
        candles = [_make_candle(1000 + i * 60_000, 100.0, 101.0, 99.5, 100.2, 100.0, 50.0) for i in range(30)]
        # Add confirmed absorption candle: heavy selling absorbed at support with resilient close
        candles.append(_make_candle(1000 + 30 * 60_000, 100.0, 100.8, 99.6, 100.5, 300.0, 70.0))
        res_cvd = compute_volume_cvd_analytics(candles)
        self.assertTrue(res_cvd.absorption_detected)

        accum = evaluate_accumulation_structure(candles, cvd_result=res_cvd)
        self.assertEqual(accum.factor_scores["absorption"], 1.0)
        self.assertTrue(any("absorption confirmed" in r.lower() for r in accum.reasons))

    def test_absorption_factor_zero_when_positive_cvd_no_absorption(self):
        """When absorption_detected is False, positive CVD slope and taker_buy_ratio 0.65 must produce 0.0."""
        candles = [_make_candle(1000 + i * 60_000, 100.0 + i * 0.1, 100.5 + i * 0.1, 99.8 + i * 0.1, 100.3 + i * 0.1, 1000.0, 650.0) for i in range(35)]
        res_cvd = compute_volume_cvd_analytics(candles)
        self.assertFalse(res_cvd.absorption_detected)
        self.assertGreater(res_cvd.cvd_slope_normalized, 0.0)
        self.assertAlmostEqual(res_cvd.taker_buy_ratio, 0.65, places=2)

        accum = evaluate_accumulation_structure(candles, cvd_result=res_cvd)
        self.assertEqual(accum.factor_scores["absorption"], 0.0, "Absorption factor must be strictly 0.0 when absorption is not detected")

    def test_positive_cvd_flow_contributes_independently_to_cvd_factor(self):
        """Positive CVD flow must contribute to Factor D (cvd_divergence) independently, with Factor C at 0.0."""
        candles = [_make_candle(1000 + i * 60_000, 100.0 + i * 0.1, 100.5 + i * 0.1, 99.8 + i * 0.1, 100.3 + i * 0.1, 1000.0, 650.0) for i in range(35)]
        res_cvd = compute_volume_cvd_analytics(candles)

        accum = evaluate_accumulation_structure(candles, cvd_result=res_cvd)
        self.assertEqual(accum.factor_scores["absorption"], 0.0)
        # Factor D receives credit for positive CVD slope (>= 0.75 for strong positive slope)
        self.assertGreaterEqual(accum.factor_scores["cvd_divergence"], 0.75)

    def test_scenario_a_runaway_trend_cannot_receive_absorption_points(self):
        """Scenario A (runaway trend with positive CVD) cannot receive absorption points without confirmed absorption."""
        candles = []
        base_t = 1_700_000_000_000
        for i in range(40):
            t = base_t + i * 3600_000
            p = 100.0 + i * 0.875
            candles.append(_make_candle(t, p - 0.5, p + 1.2, p - 1.0, p + 0.8, 2000.0, 1300.0, ct=t + 3599_000))

        cvd_res = compute_volume_cvd_analytics(candles, lookback=40)
        self.assertFalse(cvd_res.absorption_detected)

        accum = evaluate_accumulation_structure(candles, cvd_result=cvd_res, lookback=40)
        self.assertEqual(accum.factor_scores["absorption"], 0.0)
        self.assertEqual(accum.classification, "NONE")

    def test_early_accumulation_classification_behavior_preserved(self):
        """EARLY_ACCUMULATION classification remains correct when absorption is confirmed in a tight base."""
        candles = []
        base_t = 1_700_000_000_000
        for i in range(35):
            t = base_t + i * 3600_000
            low_val = 100.0 + (i / 35.0) * 1.5
            h_val = low_val + 2.0
            o_val = low_val + (1.8 if i % 3 == 0 else 0.3)
            c_val = low_val + (0.3 if i % 3 == 0 else 1.8)
            v = 400.0 if i % 3 == 0 else 1500.0
            v_tb = 150.0 if i % 3 == 0 else 1000.0
            candles.append(_make_candle(t, o_val, h_val, low_val, c_val, v, v_tb, ct=t + 3599_000))

        # Add absorption bar at bar 34
        candles[34] = _make_candle(base_t + 34 * 3600_000, 101.6, 102.8, 101.5, 102.5, 3500.0, 700.0, ct=base_t + 35 * 3600_000 - 1000)

        accum = evaluate_accumulation_structure(candles, lookback=35)
        self.assertEqual(accum.factor_scores["absorption"], 1.0)
        self.assertEqual(accum.classification, "EARLY_ACCUMULATION")
        self.assertTrue(any("absorption and base structure" in r.lower() for r in accum.reasons))


class TestOrderBookDIR(unittest.TestCase):
    """Test suite for Order Book DIR with exact boundaries and crossed book detection."""

    def test_orderbook_dir_exact_boundaries(self):
        """Orders placed at exact 1.0% and 2.0% boundaries must be included."""
        mid = 100.0
        # Exact 1.0% boundary: bid at 99.0, ask at 101.0
        # Exact 2.0% boundary: bid at 98.0, ask at 102.0
        bids = [["99.0", "10"], ["98.0", "20"]]
        asks = [["101.0", "10"], ["102.0", "20"]]
        ob = {"bids": bids, "asks": asks}

        d1, d2, st = calculate_orderbook_dir(ob, mid_price=mid)
        self.assertEqual(st, "ASSESSED")
        # 1% depth: bid = 990, ask = 1010 -> d1 = 990 / 2000 = 0.495
        self.assertAlmostEqual(d1, 990.0 / 2000.0, places=3)
        # 2% depth: bid = 990 + 1960 = 2950, ask = 1010 + 2040 = 3050 -> d2 = 2950 / 6000 = 0.4916...
        self.assertAlmostEqual(d2, 2950.0 / 6000.0, places=3)

    def test_crossed_orderbook_detection(self):
        """Crossed book (best_bid >= best_ask) must return INVALID_CROSSED_BOOK."""
        crossed_ob = {
            "bids": [["101.0", "10"]],
            "asks": [["100.0", "10"]],
        }
        d1, d2, st = calculate_orderbook_dir(crossed_ob, mid_price=100.5)
        self.assertEqual(st, "INVALID_CROSSED_BOOK")
        self.assertEqual((d1, d2), (0.5, 0.5))

    def test_invalid_levels_and_empty_depth(self):
        """Non-numeric levels skipped; empty depth returns EMPTY_DEPTH."""
        ob_invalid = {
            "bids": [["-99.0", "10"], ["abc", "10"]],
            "asks": [["101.0", "0"]],
        }
        d1, d2, st = calculate_orderbook_dir(ob_invalid, mid_price=100.0)
        self.assertEqual(st, "UNASSESSED")


class TestPhase1NonRegressionIsolation(unittest.TestCase):
    """Verify that Smart Money does not modify or corrupt existing modules."""

    def test_existing_modules_unaffected_by_smart_money_import(self):
        """Importing smart_money must not alter sls_strategy, signals, or scanner."""
        import sls_strategy
        import signals
        self.assertTrue(hasattr(sls_strategy, "evaluate_sls_coin"))
        self.assertTrue(hasattr(sls_strategy, "resample_15m_to_45m"))
        self.assertTrue(hasattr(signals, "analyze_coin_daily"))
        self.assertTrue(hasattr(signals, "intraday_signal"))


if __name__ == "__main__":
    unittest.main()
