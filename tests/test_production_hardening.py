import unittest

import discovery
import signals
import scanner
import backtest
import exposure
import entry_engine
from execution_guard import ExecutionRequest, validate_spot_request


class ProductionHardeningTests(unittest.TestCase):
    def test_structure_snapshot(self):
        closes = [100 + i * 0.2 for i in range(40)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        snap = discovery.structure_snapshot(closes, highs, lows)
        self.assertIn(snap["state"], ("BULLISH", "MIXED"))
        self.assertTrue(snap["higher_lows"])

    def test_ranking_does_not_invent_historical_edge(self):
        hits = [
            {"coin": "A", "entry_quality": 90, "score": 80, "expansion_score": 90, "rr": 2.5, "potential_pct": 40},
            {"coin": "B", "entry_quality": 60, "score": 70, "expansion_score": 60, "rr": 1.8, "potential_pct": 20},
        ]
        ranked = scanner._rank_trade_candidates(hits, {})
        self.assertEqual(ranked[0]["coin"], "A")
        self.assertGreater(ranked[0]["trade_quality"], ranked[1]["trade_quality"])

    def test_backtest_setup_stats_are_aggregateable(self):
        results = [{"symbol": "AAA", "interval": "15m", "n": 2,
                    "wins": 1, "losses": 1, "trades": [
                        {"outcome": "WIN", "setup_type": "BREAKOUT"},
                        {"outcome": "LOSS", "setup_type": "BREAKOUT"}]}]
        buckets = {}
        for result in results:
            for trade in result["trades"]:
                key = f"{result['interval']}|{trade['setup_type']}"
                b = buckets.setdefault(key, {"wins": 0, "losses": 0})
                b["wins" if trade["outcome"] == "WIN" else "losses"] += 1
        self.assertEqual(buckets["15m|BREAKOUT"]["wins"], 1)
        self.assertEqual(buckets["15m|BREAKOUT"]["losses"], 1)

    def test_correlation_measure_is_bounded(self):
        a = [0.01, -0.01, 0.02, -0.02] * 6
        b = list(a)
        c2 = [-x for x in a]
        self.assertAlmostEqual(exposure._corr(a, b), 1.0, places=6)
        self.assertAlmostEqual(exposure._corr(a, c2), -1.0, places=6)

    def test_backtest_records_expansion_milestones(self):
        rows = []
        for i in range(10):
            price = 100 + i * 6
            rows.append([i, price, price, price, price, 1000, i, 100000, 0, 0, 0, 0])
        signal = {"entry": 100.0, "stop": 90.0, "target": 130.0}
        result = backtest._evaluate(rows, 0, signal, 9)
        self.assertTrue(result["milestones"]["5"])
        self.assertTrue(result["milestones"]["10"])
        self.assertTrue(result["milestones"]["20"])
        self.assertTrue(result["milestones"]["30"])
        self.assertFalse(result["milestones"]["50"])

    def test_spot_guard_requires_confirmation_and_rejects_derivatives(self):
        with self.assertRaises(PermissionError):
            validate_spot_request(ExecutionRequest("SPOT", "BUY", "BTCUSDT", "BINANCE", 1))
        with self.assertRaises(ValueError):
            validate_spot_request(ExecutionRequest("FUTURES", "BUY", "BTCUSDT", "BINANCE", 1, True))


    def test_realtime_cache_accepts_only_closed_klines(self):
        cache = __import__("realtime").BinanceKlineCache(["BTC"], intervals=["5m"], max_bars=60)
        raw = '{"data":{"k":{"x":false,"s":"BTCUSDT","i":"5m","t":1,"o":"1","h":"2","l":"0.5","c":"1.5","v":"10","q":"15"}}}'
        cache._on_message(None, raw)
        self.assertEqual(cache.get("BTC", "5m"), [])
        raw = '{"data":{"k":{"x":true,"s":"BTCUSDT","i":"5m","t":1,"o":"1","h":"2","l":"0.5","c":"1.5","v":"10","q":"15"}}}'
        cache._on_message(None, raw)
        self.assertEqual(len(cache.get("BTC", "5m")), 1)
        self.assertEqual(cache.get("BTC", "5m")[0]["close"], 1.5)


    def test_early_expansion_classifier_is_causal_and_penalizes_extension(self):
        from expansion import classify_expansion
        closes = [100 + i * 0.05 for i in range(30)]
        highs = [c * 1.002 for c in closes]
        lows = [c * 0.998 for c in closes]
        volumes = [100.0] * 30
        result = classify_expansion(closes, highs, lows, volumes, atr=1.0)
        self.assertIn(result["state"], {"BASE", "EXPANSION", "EARLY_EXPANSION"})
        self.assertEqual(result["extension_pct"], round((closes[-1] / min(lows[-20:]) - 1) * 100, 2))
        self.assertNotIn("future", result)

    def test_confirmed_entry_requires_sweep_bos_retest_confirmation(self):
        n = 50
        closes = [100.0] * n
        opens = [99.8] * n
        highs = [105.0] * n
        lows = [95.0] * n
        for i in range(0, 41):
            closes[i] = 100.0
            opens[i] = 99.5
            highs[i] = 105.0
            lows[i] = 95.0
        # Sell-side liquidity sweep/reclaim.
        lows[41], closes[41], opens[41], highs[41] = 94.0, 98.0, 96.0, 101.0
        # Bullish BOS above the pre-sweep high.
        opens[42], closes[42], highs[42], lows[42] = 100.0, 107.0, 108.0, 99.0
        # Retest of the broken 105 level.
        opens[43], closes[43], highs[43], lows[43] = 104.0, 106.0, 107.0, 104.0
        # Confirmation candle.
        opens[44], closes[44], highs[44], lows[44] = 106.0, 109.0, 110.0, 105.5
        for i in range(45, n):
            opens[i], closes[i], highs[i], lows[i] = 108.0, 109.0, 110.0, 107.0

        result = entry_engine.evaluate_entry(closes, highs, lows, opens, "15m", atr=2.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_trigger"], "SWEEP_RECLAIM_BOS_RETEST_CONFIRM")
        self.assertTrue(result["liquidity_sweep_confirmed"])
        self.assertTrue(result["bos_confirmed"])
        self.assertTrue(result["retest_confirmed"])
        self.assertTrue(result["confirmation_candle"])
        self.assertGreaterEqual(result["entry_quality"], 80)

    def test_target_staging_is_monotonic(self):
        target = 120.0
        entry = 100.0
        t1 = entry + (target - entry) * 0.35
        t2 = entry + (target - entry) * 0.65
        self.assertLess(entry, t1)
        self.assertLess(t1, t2)
        self.assertLess(t2, target)


    def test_adaptive_thresholds_ignore_backtest_only_stats(self):
        import adaptive
        result = adaptive.adaptive_thresholds(
            "15m", "MOMENTUM", 60, 1.25, 1.70,
            {"sample": 40, "source": "backtest", "win_pct": 95,
             "avg_mfe_pct": 20, "avg_mae_pct": -1,
             "milestone_rates": {"5": 95, "10": 90, "20": 80}},
            {"adaptive_min_score_floor": 55,
             "adaptive_min_vol_x_floor": 1.15,
             "adaptive_min_rr_floor": 1.50},
        )
        self.assertEqual(result["mode"], "BASE")

    def test_adaptive_thresholds_can_relax_to_configured_floors(self):
        import adaptive
        result = adaptive.adaptive_thresholds(
            "15m", "MOMENTUM", 60, 1.25, 1.70,
            {"sample": 40, "source": "live", "win_pct": 90,
             "avg_mfe_pct": 20, "avg_mae_pct": -0.5,
             "milestone_rates": {"5": 95, "10": 90, "20": 80}},
            {"adaptive_min_score_floor": 55,
             "adaptive_min_vol_x_floor": 1.15,
             "adaptive_min_rr_floor": 1.50},
        )
        self.assertEqual(result["mode"], "ADAPTIVE")
        self.assertGreaterEqual(result["min_score"], 55)
        self.assertGreaterEqual(result["min_vol_x"], 1.15)
        self.assertGreaterEqual(result["min_rr"], 1.50)

    def test_adaptive_thresholds_can_be_disabled(self):
        import adaptive
        result = adaptive.adaptive_thresholds(
            "15m", "MOMENTUM", 60, 1.25, 1.70,
            {"sample": 40, "source": "live", "win_pct": 90},
            {"adaptive_thresholds_enabled": False},
        )
        self.assertEqual(result["mode"], "BASE")

    def test_component_attribution_requires_minimum_sample(self):
        from outcome_attribution import aggregate
        rows = []
        for i in range(20):
            rows.append({
                "outcome": "WIN" if i < 15 else "LOSS",
                "component_flags": {"compression": True, "bos": i < 10},
                "milestones": {"5": i < 16, "10": i < 12, "20": i < 4},
            })
        stats = aggregate(rows, min_samples=20)
        self.assertIn("compression", stats)
        self.assertNotIn("bos", stats)
        self.assertEqual(stats["compression"]["sample"], 20)
        self.assertEqual(stats["compression"]["milestone_rates"]["5"], 80.0)

    def test_adaptive_thresholds_need_sample_and_stay_bounded(self):
        import adaptive
        base = adaptive.adaptive_thresholds("15m", "MOMENTUM", 60, 1.25, 1.70,
                                           {"sample": 10, "win_pct": 100}, {})
        self.assertEqual(base["mode"], "BASE")
        strong = adaptive.adaptive_thresholds(
            "15m", "MOMENTUM", 60, 1.25, 1.70,
            {"sample": 30, "win_pct": 80,
             "avg_mfe_pct": 12, "avg_mae_pct": -1,
             "milestone_rates": {"5": 90, "10": 80, "20": 60},
             "scope": "setup/timeframe"},
            {"adaptive_min_score_floor": 58,
             "adaptive_min_vol_x_floor": 1.20,
             "adaptive_min_rr_floor": 1.60},
        )
        self.assertEqual(strong["mode"], "ADAPTIVE")
        self.assertGreaterEqual(strong["min_score"], 58)
        self.assertGreaterEqual(strong["min_vol_x"], 1.20)
        self.assertGreaterEqual(strong["min_rr"], 1.60)
        weak = adaptive.adaptive_thresholds(
            "15m", "MOMENTUM", 60, 1.25, 1.70,
            {"sample": 30, "win_pct": 30,
             "avg_mfe_pct": 2, "avg_mae_pct": -8,
             "milestone_rates": {"5": 20, "10": 10, "20": 5}},
            {"adaptive_min_score_floor": 58,
             "adaptive_min_vol_x_floor": 1.20,
             "adaptive_min_rr_floor": 1.60},
        )
        self.assertGreaterEqual(weak["min_score"], 60)
        self.assertGreaterEqual(weak["min_vol_x"], 1.25)
        self.assertGreaterEqual(weak["min_rr"], 1.70)


if __name__ == "__main__":
    unittest.main()
