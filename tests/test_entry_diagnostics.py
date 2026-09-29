import unittest

from entry_engine import entry_diagnostics


class EntryDiagnosticsTests(unittest.TestCase):
    def test_insufficient_bars_is_not_ranked_as_near_miss(self):
        rows = [100.0] * 20
        out = entry_diagnostics(rows, rows, rows, rows, "5m", atr=1.0)
        self.assertEqual(out["near_miss_score"], 0.0)
        self.assertEqual(out["reason"], "insufficient_bars")

    def test_bos_without_retest_has_partial_stage_progress(self):
        n = 70
        closes = [100.0] * n
        highs = [100.0] * n
        lows = [100.0] * n
        opens = [100.0] * n
        # Liquidity sweep, followed by a BOS above the swept range.
        lows[60] = 98.0
        closes[60] = 101.0
        highs[60] = 101.0
        closes[67] = 102.0
        highs[67] = 102.0
        out = entry_diagnostics(closes, highs, lows, opens, "5m", atr=1.0)
        self.assertTrue(out["bos_confirmed"])
        self.assertFalse(out["retest_confirmed"])
        self.assertEqual(out["near_miss_score"], 35.0)
        self.assertEqual(out["reason"], "awaiting_retest")

    def test_bos_and_retest_measure_confirmation_gap(self):
        n = 70
        closes = [100.0] * n
        highs = [100.0] * n
        lows = [100.0] * n
        opens = [100.0] * n
        lows[60] = 98.0
        closes[60] = 101.0
        highs[60] = 101.0
        closes[67] = 102.0
        highs[67] = 102.0
        # Retest of the BOS level, followed by a weak bearish candle.
        lows[68] = 99.9
        closes[68] = 101.0
        opens[68] = 101.1
        highs[68] = 101.2
        opens[69] = 101.1
        closes[69] = 100.8
        highs[69] = 101.2
        lows[69] = 100.7
        out = entry_diagnostics(closes, highs, lows, opens, "5m", atr=1.0)
        self.assertTrue(out["bos_confirmed"])
        self.assertTrue(out["retest_confirmed"])
        self.assertFalse(out["confirmation_candle"])
        self.assertGreaterEqual(out["near_miss_score"], 70.0)
        self.assertFalse(out["confirmation_direction_bullish"])
        self.assertEqual(out["confirmation_body_gap"], 0.0)
        self.assertEqual(out["reason"], "confirmation_missing")


if __name__ == "__main__":
    unittest.main()
