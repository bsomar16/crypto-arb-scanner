import unittest

from signal_context import _zero_inverse, _order_block, _volatility


class SignalContextTests(unittest.TestCase):
    def test_zero_inverse_bullish_reclaim(self):
        closes = [100.0] * 40
        closes += [99.0, 98.5, 98.0, 98.8, 99.7, 100.8, 101.5, 102.0]
        result = _zero_inverse(closes)
        self.assertTrue(result["bullish_reclaim"] or result["bullish_reversal"])
        self.assertIn(result["state"], {"BULLISH_REVERSAL", "BULLISH"})

    def test_volatility_reports_expansion(self):
        closes = [100.0 + i * 0.02 for i in range(70)]
        highs = [x + 0.2 for x in closes]
        lows = [x - 0.2 for x in closes]
        highs[-5:] = [x + 1.2 for x in closes[-5:]]
        lows[-5:] = [x - 1.2 for x in closes[-5:]]
        result = _volatility(highs, lows, closes, 0.4)
        self.assertGreater(result["atr_ratio"], 1.0)
        self.assertIn(result["state"], {"EXPANDING", "NORMAL"})

    def test_order_block_is_context_only(self):
        closes = [100.0] * 35
        highs = [100.2] * 35
        lows = [99.8] * 35
        vols = [1000.0] * 35
        closes[20] = 99.5
        lows[20] = 99.0
        highs[20] = 100.0
        closes[21] = 101.5
        highs[21] = 102.0
        closes[22] = 102.5
        highs[22] = 103.0
        result = _order_block(closes, highs, lows, vols, 0.8)
        self.assertIsInstance(result["bullish"], bool)
        self.assertIn("strength", result)


if __name__ == "__main__":
    unittest.main()
