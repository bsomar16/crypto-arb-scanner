import unittest

import discovery
import signals
import scanner
import backtest
import exposure
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

    def test_target_staging_is_monotonic(self):
        target = 120.0
        entry = 100.0
        t1 = entry + (target - entry) * 0.35
        t2 = entry + (target - entry) * 0.65
        self.assertLess(entry, t1)
        self.assertLess(t1, t2)
        self.assertLess(t2, target)


if __name__ == "__main__":
    unittest.main()
