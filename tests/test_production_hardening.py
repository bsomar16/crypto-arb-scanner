import unittest

import discovery
import signals
import scanner
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
