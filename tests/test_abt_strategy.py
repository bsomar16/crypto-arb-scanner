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
            highs[i] = 108.0
            lows[i] = 103.0

        # Bearish high-volume climax two candles before the decision candle.
        closes[77] = 100.4
        opens[77] = 103.0
        highs[77] = 108.0
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
                "target_optimization_enabled": False,
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy_family"], "ABT~")
        self.assertTrue(result["abt"]["absorption_confirmed"])
        self.assertEqual(result["abt"]["shakeout_index"], 77)

    def test_structural_target_avoids_artificial_five_percent_floor(self):
        target = abt_strategy._base_targets(
            100.0,
            [100.5, 101.0, 102.0, 101.0, 103.0, 107.5, 105.0, 108.0, 109.0, 108.0],
            atr=1.0,
            max_potential=80.0,
        )
        self.assertGreaterEqual(target, 107.5)

    def test_structural_target_ladder_prefers_real_intermediate_resistance(self):
        price = 100.0
        highs = [102.0, 103.0, 104.0, 106.0, 107.5, 110.0]
        target = abt_strategy._base_targets(
            price, highs, atr=1.0, max_potential=80.0, min_potential=5.0
        )
        self.assertEqual(target, 106.0)
        resistance_levels = sorted({
            round(float(h), 12)
            for h in highs[-120:-1]
            if price < float(h) < target
        })
        ladder = resistance_levels[-2:] + [target]
        self.assertEqual(ladder, [103.0, 104.0, 106.0])

    def test_structural_target_ignores_unconfirmed_wick_resistance(self):
        target = abt_strategy._base_targets(
            100.0,
            [100.5, 101.0, 105.5, 102.0, 103.0, 104.0, 103.0],
            atr=1.0,
            max_potential=80.0,
            min_potential=5.0,
        )
        self.assertEqual(target, 0.0)

    def test_structural_target_returns_zero_when_no_resistance_meets_floor(self):
        target = abt_strategy._base_targets(
            100.0,
            [100.5, 101.0, 102.0, 103.5, 104.0, 103.0, 102.5],
            atr=1.0,
            max_potential=80.0,
            min_potential=5.0,
        )
        self.assertEqual(target, 0.0)

    def test_abt_mtf_context_exposes_all_supported_timeframes(self):
        closes = [100.0 + i * 0.2 for i in range(100)]
        states = abt_strategy._mtf_alignment(
            "15m",
            closes,
            {tf: closes for tf in ("5m", "1h", "4h", "1d", "1w")},
        )
        self.assertEqual(set(states["states"]), {"5m", "15m", "1h", "4h", "1d", "1w"})
        self.assertEqual(states["states"]["15m"], "BULLISH")
        self.assertEqual(states["states"]["1d"], "BULLISH")
        self.assertEqual(states["alignment"], "BULLISH")

    def test_abt_closed_lifecycle_starts_new_opportunity(self):
        import tempfile
        import signal_lifecycle

        original = signal_lifecycle.PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                signal_lifecycle.PATH = f"{td}/signal_lifecycle.json"
                base = {"coin": "TEST", "interval": "15m", "strategy": "ABT",
                        "strategy_family": "ABT", "setup_type": "TRENDLINE_BREAKOUT",
                        "entry": 100.0, "t3": 110.0, "candle_open_time": 1000}
                key1 = signal_lifecycle.register(base)
                signal_lifecycle.transition(key1, "CLOSED", event="tp3")
                key2 = signal_lifecycle.register(dict(base, candle_open_time=2000, entry=105.0))
                self.assertNotEqual(key1, key2)
                self.assertTrue(key2.endswith("|2000"))
        finally:
            signal_lifecycle.PATH = original

    def test_abt_lifecycle_stages_share_one_timestamped_opportunity(self):
        import tempfile
        import signal_lifecycle

        original = signal_lifecycle.PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                signal_lifecycle.PATH = f"{td}/signal_lifecycle.json"
                base = {"coin": "TEST", "interval": "15m", "strategy": "ABT",
                        "strategy_family": "ABT~", "setup_type": "SHAKEOUT_ABSORPTION",
                        "entry": 100.0, "t3": 110.0, "candle_open_time": 1000}
                key1 = signal_lifecycle.register(base)
                key2 = signal_lifecycle.register(dict(
                    base, strategy_family="ABT*", setup_type="DEMAND_REVERSAL",
                    entry=101.0, candle_open_time=1015,
                ))
                key3 = signal_lifecycle.register(dict(
                    base, strategy_family="ABT", setup_type="TRENDLINE_BREAKOUT",
                    entry=102.0, candle_open_time=1030,
                ))
                self.assertEqual(key1, key2)
                self.assertEqual(key2, key3)
                self.assertTrue(key1.endswith("|1000"))
                row = signal_lifecycle._read()[key1]
                self.assertEqual(row["stage"], "ABT")
                self.assertEqual(
                    [x["stage"] for x in row["stage_history"]],
                    ["ABT*", "ABT"],
                )
        finally:
            signal_lifecycle.PATH = original


    def test_abt_overextension_is_rejected(self):
        n = 80
        closes = [100.0 + i * 0.05 for i in range(n)]
        highs = [x + 1.0 for x in closes]
        lows = [x - 1.0 for x in closes]
        opens = [x - 0.2 for x in closes]
        volumes = [100.0] * n
        # Force a valid-looking reversal candle, then reject on the 24h move gate.
        lows[-1] = closes[-1] - 2.0
        opens[-1] = closes[-1] - 0.5
        result = abt_strategy.evaluate_abt(
            closes, highs, lows, opens, volumes,
            interval="15m",
            atr=1.0,
            cfg={
                "signal_min_potential_pct": 5,
                "signal_min_rr": 1.5,
                "abt_max_chg24_pct": 50,
            },
            chg24=75.0,
        )
        self.assertIsNone(result)

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
