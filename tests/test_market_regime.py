import unittest
from market_regime import classify_regime


def trend_prices(n=80, slope=0.01):
    return [100.0 * ((1.0 + slope) ** i) for i in range(n)]


class MarketRegimeTests(unittest.TestCase):
    def test_bull_expansion(self):
        r = classify_regime(trend_prices(slope=0.012), 70.0, 2.0)
        self.assertEqual(r["state"], "BULL_EXPANSION")
        self.assertEqual(r["score_modifier"], 4.0)

    def test_bear_expansion(self):
        r = classify_regime(trend_prices(slope=-0.012), 30.0, 2.0)
        self.assertEqual(r["state"], "BEAR_EXPANSION")
        self.assertEqual(r["score_modifier"], -4.0)

    def test_sideways(self):
        prices = [100 + (1 if i % 2 else -1) for i in range(80)]
        r = classify_regime(prices, 50.0, 1.8)
        self.assertEqual(r["state"], "SIDEWAYS")

    def test_unknown_is_neutral(self):
        r = classify_regime([100, 101, 102], 80.0, 5.0)
        self.assertEqual(r["state"], "UNKNOWN")
        self.assertEqual(r["score_modifier"], 0.0)


if __name__ == "__main__":
    unittest.main()
