#!/usr/bin/env python3
"""Regression tests for horizon-aware TP limits and milestone trailing stops."""

import unittest

import signals
import positions
import target_quality


class TestHorizonTPCeilings(unittest.TestCase):
    def test_all_supported_horizons_have_explicit_caps(self):
        expected = {
            "5m": 20.0,
            "15m": 35.0,
            "1h": 60.0,
            "4h": 100.0,
            "1d": 200.0,
            "1w": 300.0,
        }
        self.assertEqual(signals.TP_POTENTIAL_CEILINGS, expected)

    def test_target_optimizer_never_exceeds_horizon_cap(self):
        stats = {
            "overall": {
                "sample": 1000,
                "hit_rates_pct": {"t1": 99.0, "t2": 99.0, "t3": 99.0},
            }
        }
        for interval, cap in signals.TP_POTENTIAL_CEILINGS.items():
            signal = {
                "interval": interval,
                "setup_type": "BREAKOUT",
                "entry": 100.0,
                "t1": 110.0,
                "t2": 120.0,
                "t3": 500.0,
                "target": 500.0,
                "max_potential_pct": 999.0,
                "min_potential_pct": 5.0,
                "risk_pct": 2.0,
                "rr": 1.5,
            }
            plan = target_quality.optimize_targets(signal, stats)
            potential = (plan["t3"] / signal["entry"] - 1.0) * 100.0
            self.assertLessEqual(potential, cap + 1e-9, interval)


class TestTrailingStopSafety(unittest.TestCase):
    @staticmethod
    def position():
        return {
            "entry": 100.0,
            "sl": 95.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "tp3": 140.0,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }

    def test_tp1_ratchets_stop_up(self):
        pos = self.position()
        pos["tp1_hit"] = True
        self.assertTrue(positions._trail_stop_after_tp(pos, 115.0))
        self.assertEqual(pos["trailing_stop_stage"], "TP1_TRAIL")
        self.assertAlmostEqual(pos["sl"], 112.5)

    def test_tp2_ratchets_runner_stop_up(self):
        pos = self.position()
        pos["tp1_hit"] = True
        pos["tp2_hit"] = True
        pos["sl"] = 112.5
        self.assertTrue(positions._trail_stop_after_tp(pos, 135.0))
        self.assertEqual(pos["trailing_stop_stage"], "TP2_RUNNER")
        self.assertAlmostEqual(pos["sl"], 130.0)

    def test_stop_never_moves_down(self):
        pos = self.position()
        pos["tp1_hit"] = True
        self.assertTrue(positions._trail_stop_after_tp(pos, 115.0))
        first_sl = pos["sl"]
        self.assertFalse(positions._trail_stop_after_tp(pos, 113.0))
        self.assertEqual(pos["sl"], first_sl)

    def test_stop_never_crosses_current_price(self):
        pos = self.position()
        pos["tp1_hit"] = True
        self.assertFalse(positions._trail_stop_after_tp(pos, 112.5))
        self.assertEqual(pos["sl"], 95.0)


if __name__ == "__main__":
    unittest.main()
