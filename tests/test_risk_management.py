import unittest
from risk_management import DynamicRiskManager
import entry_engine


class DynamicRiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.mgr = DynamicRiskManager(config={"account_equity_usdt": 10000.0})

    def test_calculate_position_size_handles_none_win_rate(self):
        sig = {
            "coin": "SOL",
            "entry": 100.0,
            "stop": 95.0,
            "target": 115.0,
            "historical_win_pct": None,
            "regime_position_mult": None,
            "ml_boost": None,
        }
        pos = self.mgr.calculate_position_size(sig)
        self.assertIsNotNone(pos)
        self.assertEqual(pos.coin, "SOL")
        self.assertGreater(pos.size_usdt, 0)
        self.assertGreater(pos.risk_usdt, 0)

    def test_calculate_position_size_invalid_prices(self):
        self.assertIsNone(self.mgr.calculate_position_size({"entry": 0, "stop": 10}))
        self.assertIsNone(self.mgr.calculate_position_size({"entry": 10, "stop": 0}))
        self.assertIsNone(self.mgr.calculate_position_size({"entry": 10, "stop": 10}))


class EntryEngineTests(unittest.TestCase):
    def test_evaluate_entry_without_retest_and_sweep(self):
        # 50 candles with a clean breakout
        closes = [10.0 + i * 0.1 for i in range(50)]
        opens = [c - 0.05 for c in closes]
        highs = [c + 0.1 for c in closes]
        lows = [o - 0.1 for o in opens]
        # Make the last 3 candles break out sharply
        highs[-3] = 16.0
        closes[-3] = 15.8
        highs[-2] = 16.5
        closes[-2] = 16.3
        highs[-1] = 17.0
        closes[-1] = 16.8

        res = entry_engine.evaluate_entry(
            closes, highs, lows, opens, "15m",
            require_retest=False,
            require_sweep=False,
        )
        self.assertIsNotNone(res)
        self.assertIn("entry_price", res)
        self.assertIn("bos_confirmed", res)


if __name__ == "__main__":
    unittest.main()
