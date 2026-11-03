import unittest
from unittest.mock import patch

import sls_strategy


class SlsStrategyTests(unittest.TestCase):
    def test_requested_timeframe_mapping(self):
        self.assertEqual(sls_strategy.INTERVALS, ("5m", "15m", "1h", "4h", "1d", "1w"))
        self.assertEqual(sls_strategy.HTF_MAP, {
            "5m": "1h", "15m": "4h", "1h": "1d", "4h": "1w", "1d": "1M", "1w": None
        })

    def test_sensitivity_thresholds(self):
        self.assertEqual(sls_strategy.THRESHOLDS["Conservative"], 90)
        self.assertEqual(sls_strategy.THRESHOLDS["Balanced"], 86)
        self.assertEqual(sls_strategy.THRESHOLDS["Aggressive"], 82)

    def test_pivots_only_return_confirmed_swing_lows(self):
        values = [5, 4, 3, 4, 5, 6, 5, 4, 3, 4, 5]
        pivots = sls_strategy._pivot_lows(values, left=2, right=2)
        self.assertEqual([p[0] for p in pivots], [2, 8])

    def test_unknown_timeframe_fails_closed_without_fetch(self):
        with patch.object(sls_strategy, "_candles") as fetch:
            self.assertIsNone(sls_strategy.evaluate_sls_coin("ETH", "2h", {}))
            fetch.assert_not_called()

    def test_missing_market_data_fails_closed(self):
        with patch.object(sls_strategy, "_candles", return_value=None):
            self.assertIsNone(sls_strategy.evaluate_sls_coin("ETH", "5m", {}))

    def test_signal_requires_btc_benchmark_data(self):
        # With no BTC data, the benchmark gate cannot be passed.
        import math
        rows = []
        base = 100.0
        for i in range(240):
            close = base + i * 0.15 + math.sin(i / 4) * 0.1
            rows.append({"t": i, "o": close - 0.03, "h": close + 0.08,
                         "l": close - 0.08, "c": close, "v": 1000 + i,
                         "q": 100000 + i * 100})
        def fetch(symbol, interval, limit=240):
            if symbol == "BTC":
                return None
            if interval == "1h":
                return rows
            return rows
        with patch.object(sls_strategy, "_candles", side_effect=fetch):
            self.assertIsNone(sls_strategy.evaluate_sls_coin("ETH", "1w", {"sls_min_score": 0}))


if __name__ == "__main__":
    unittest.main()
