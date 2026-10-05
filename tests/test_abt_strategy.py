import unittest

import abt_strategy


class ABTToolkitTests(unittest.TestCase):
    def test_pivots_are_not_available_before_right_bars(self):
        highs = [10.0] * 20
        highs[10] = 20.0
        # A pivot at index 10 cannot exist until index 15 is available.
        self.assertNotIn((10, 20.0), abt_strategy._pivot_highs(highs, 5, 5, upto=14))
        self.assertIn((10, 20.0), abt_strategy._pivot_highs(highs, 5, 5, upto=15))

    def test_descending_trendline_requires_lower_second_high(self):
        highs = [10.0] * 40
        highs[10] = 20.0
        highs[20] = 18.0
        line = abt_strategy._descending_trendline(highs, 2, 2, require_untouched=True)
        self.assertIsNotNone(line)
        self.assertLess(line["newer_price"], line["older_price"])

    def test_demand_zone_is_half_atr_above_confirmed_pivot_low(self):
        lows = [10.0] * 40
        lows[20] = 8.0
        zone = abt_strategy._demand_zone(lows, atr=2.0, left=2, right=2)
        self.assertIsNotNone(zone)
        self.assertEqual(zone["bottom"], 8.0)
        self.assertEqual(zone["top"], 9.0)

    def test_abt_shakeout_absorption_stage(self):
        n = 80
        closes = [110.0] * n
        highs = [111.0] * n
        lows = [109.0] * n
        opens = [110.0] * n
        volumes = [100.0] * n

        # Confirmed demand pivot at 60; its pivot-right bars are 61-65.
        closes[60] = 100.0
        opens[60] = 101.0
        highs[60] = 101.0
        lows[60] = 99.0
        for i in range(61, 66):
            closes[i] = 105.0
            opens[i] = 104.0
            highs[i] = 106.0
            lows[i] = 103.0

        # Bearish high-volume climax two candles before the decision candle.
        closes[77] = 100.4
        opens[77] = 103.0
        highs[77] = 103.2
        lows[77] = 99.8
        volumes[77] = 250.0

        # Quiet bullish absorption candle; narrow and above the climax low.
        closes[79] = 101.0
        opens[79] = 100.8
        highs[79] = 101.2
        lows[79] = 100.7
        volumes[79] = 50.0

        result = abt_strategy.evaluate_abt(
            closes, highs, lows, opens, volumes,
            interval="15m",
            atr=2.0,
            higher_closes=[100.0 + i * 0.05 for i in range(100)],
            cfg={
                "abt_min_score_early": 40,
                "abt_min_score_reversal": 40,
                "abt_min_score_breakout": 40,
                "signal_min_potential_pct": 5,
                "signal_min_rr": 1.0,
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy_family"], "ABT~")
        self.assertTrue(result["abt"]["absorption_confirmed"])
        self.assertEqual(result["abt"]["shakeout_index"], 77)

    def test_no_stage_when_latest_candle_does_not_confirm_setup(self):
        n = 80
        closes = [110.0] * n
        highs = [111.0] * n
        lows = [109.0] * n
        opens = [110.0] * n
        volumes = [100.0] * n
        self.assertIsNone(
            abt_strategy.evaluate_abt(
                closes, highs, lows, opens, volumes,
                interval="15m", atr=2.0,
                cfg={"signal_min_potential_pct": 5, "signal_min_rr": 1.5},
            )
        )


if __name__ == "__main__":
    unittest.main()
