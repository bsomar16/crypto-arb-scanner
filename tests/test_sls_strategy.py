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

    def test_pivot_highs_are_confirmed(self):
        values = [1, 2, 3, 2, 1, 2, 4, 2, 1]
        self.assertEqual(sls_strategy._pivot_highs(values, 2, 2), [(2, 3), (6, 4)])

    def test_structural_potential_uses_nearest_confirmed_resistance(self):
        highs = [10, 11, 12, 11, 10, 11, 13, 11, 10, 12, 14, 12, 11]
        target, pct, reference = sls_strategy._structural_potential(10.5, 1.0, highs, 20.0)
        self.assertEqual(reference, "CONFIRMED_SWING_HIGH")
        self.assertAlmostEqual(target, 12.75)
        self.assertAlmostEqual(pct, (12.75 / 10.5 - 1) * 100)

    def test_structural_potential_falls_back_to_atr_target(self):
        target, pct, reference = sls_strategy._structural_potential(
            100, 2, [99, 100, 101, 100, 99], 112
        )
        self.assertEqual(reference, "ATR_TP3_FALLBACK")
        self.assertEqual(target, 112)
        self.assertAlmostEqual(pct, 12.0)



    def test_htf_trend_state_uses_pine_ema_series_with_50_candles(self):
        rows = [{"t": i, "o": 100 + i, "h": 101 + i, "l": 99 + i,
                 "c": 100 + i, "v": 1000, "q": 100000}
                for i in range(50)]
        self.assertIsNone(sls_strategy._state(rows))
        state = sls_strategy._state(rows, min_history=50)
        self.assertIsNotNone(state)
        self.assertIsNotNone(state["e200"][-1])

    def test_live_candle_parser_keeps_latest_closed_row_and_drops_forming_row(self):
        raw = [
            [0, "1", "2", "0.5", "1.5", "10", 99, "15"],
            [100, "1.5", "2", "1", "1.8", "12", 199, "20"],
        ]
        rows = sls_strategy._parse_closed_candles(raw, now_ms=150)
        self.assertEqual([row["t"] for row in rows], [0])
        self.assertEqual(rows[0]["q"], 15.0)

    def test_wilder_atr_uses_rma_not_rolling_sma(self):
        highs = [11, 12, 12, 13]
        lows = [9, 10, 9, 11]
        closes = [10, 11, 10, 12]
        self.assertAlmostEqual(
            sls_strategy._wilder_atr(highs, lows, closes, period=2), 2.75
        )

    def test_wilder_atr_requires_seed_history(self):
        self.assertIsNone(
            sls_strategy._wilder_atr([2, 3], [0, 1], [1, 2], period=2)
        )

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


    def test_ema200_state_requires_200_closed_candles(self):
        rows = [{"t": i, "o": 100 + i, "h": 101 + i, "l": 99 + i,
                 "c": 100 + i, "v": 1000, "q": 100000}
                for i in range(200)]
        self.assertIsNone(sls_strategy._state(rows[:199]))
        self.assertIsNotNone(sls_strategy._state(rows))


if __name__ == "__main__":
    unittest.main()
