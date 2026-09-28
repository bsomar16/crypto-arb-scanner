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
        closes[61] = 102.0
        highs[61] = 102.0
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
        closes[61] = 102.0
        highs[61] = 102.0
        # Retest of the BOS level, followed by a weak bearish candle.
        lows[62] = 99.9
        closes[62] = 100.1
        opens[62] = 100.2
        highs[62] = 100.3
        opens[63] = 100.2
        closes[63] = 99.9
        highs[63] = 100.3
        lows[63] = 99.8
        out = entry_diagnostics(closes, highs, lows, opens, "5m", atr=1.0)
        self.assertTrue(out["bos_confirmed"])
        self.assertTrue(out["retest_confirmed"])
        self.assertFalse(out["confirmation_candle"])
        self.assertGreaterEqual(out["near_miss_score"], 70.0)
        self.assertGreater(out["confirmation_body_gap"], 0.0)
        self.assertEqual(out["reason"], "confirmation_missing")


if __name__ == "__main__":
    unittest.main()
