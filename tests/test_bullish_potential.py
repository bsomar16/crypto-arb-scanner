import unittest

from bullish_potential import classify_bullish_potential


class BullishPotentialTests(unittest.TestCase):
    def _strong_inputs(self):
        return {
            "interval": "1h",
            "trend": {"state": "BULLISH", "interval": "4h"},
            "structure": {
                "bos": True,
                "higher_lows": True,
                "compression": 0.25,
            },
            "expansion": {"state": "EARLY_EXPANSION"},
            "context": {
                "volatility": {"state": "EXPANDING"},
                "zero_inverse": {"bullish_reversal": True, "bullish_reclaim": True},
                "order_block": {"bullish": True, "fresh": True},
            },
            "entry": {
                "bos_confirmed": True,
                "retest_confirmed": True,
                "confirmation_candle": True,
            },
            "score": 82,
            "potential_pct": 12,
        }

    def test_strong_multitimeframe_setup_is_explosive_continuation(self):
        result = classify_bullish_potential(**self._strong_inputs())
        self.assertEqual(result["tier"], "EXPLOSIVE_CONTINUATION")
        self.assertGreaterEqual(result["score"], 78)
        self.assertIn("early expansion", result["drivers"])
        self.assertIn("higher_timeframe_bullish", result["flags"])

    def test_bearish_higher_timeframe_cannot_become_explosive(self):
        values = self._strong_inputs()
        values["trend"] = {"state": "BEARISH", "interval": "4h"}
        result = classify_bullish_potential(**values)
        self.assertEqual(result["tier"], "STANDARD")

    def test_late_extension_cannot_be_explosive(self):
        values = self._strong_inputs()
        values["expansion"] = {"state": "LATE_EXTENSION"}
        result = classify_bullish_potential(**values)
        self.assertEqual(result["tier"], "STANDARD")


if __name__ == "__main__":
    unittest.main()
