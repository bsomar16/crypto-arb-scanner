import unittest
from entry_engine import _body_strength, entry_diagnostics, evaluate_entry

class EntryQualityTests(unittest.TestCase):
    def test_body_strength_is_bounded_for_normal_candle(self):
        self.assertAlmostEqual(_body_strength(100, 110, 99, 108), 8/11)

    def test_body_strength_handles_zero_range(self):
        self.assertEqual(_body_strength(100, 100, 100, 100), 0.0)

    def test_confirmation_can_follow_a_weak_first_retest(self):
        closes = [100.0] * 45 + [102.0, 101.0, 102.0]
        opens = [100.0] * 45 + [100.0, 101.05, 100.5]
        highs = [101.0] * 45 + [102.2, 101.2, 102.4]
        lows = [99.0] * 45 + [99.8, 100.8, 100.3]
        result = evaluate_entry(
            closes, highs, lows, opens, "15m", atr=1.0,
            require_retest=True, require_sweep=False,
            allow_early_retest=False, confirmation_body_min=0.35,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_trigger"], "BOS_RETEST_CONFIRM")
        self.assertEqual(result["trigger_index"], 47)

    def test_diagnostics_identifies_recoverable_later_retest(self):
        closes = [100.0] * 45 + [101.0, 102.0, 101.2, 102.0]
        opens = [100.0] * 45 + [100.0, 100.5, 101.25, 101.36]
        highs = [100.5] * 45 + [101.2, 102.4, 101.2, 102.0]
        lows = [99.5] * 45 + [99.8, 100.3, 100.8, 100.0]
        result = entry_diagnostics(
            closes, highs, lows, opens, "15m", atr=1.0,
            allow_early_retest=True,
            early_retest_body_min=0.30,
        )
        self.assertEqual(result["retest_count"], 2)
        self.assertEqual(result["confirmed_retest_count"], 2)
        self.assertTrue(result["recoverable_later_retest"])
        self.assertEqual(result["best_confirmation_index"], 48)
        self.assertEqual(result["reason"], "recoverable_later_retest")

if __name__ == "__main__":
    unittest.main()
