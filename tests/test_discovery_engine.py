import unittest

from discovery import structure_snapshot
import scanner


class DiscoveryEngineTests(unittest.TestCase):
    def test_bullish_structure_detects_higher_lows(self):
        closes = [100 + i * 0.2 for i in range(40)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        snap = structure_snapshot(closes, highs, lows)
        self.assertIn(snap["state"], ("BULLISH", "MIXED"))

    def test_trade_ranking_prefers_entry_quality_and_expansion(self):
        cfg = {}
        hits = [
            {"coin": "A", "entry_quality": 90, "score": 80, "expansion_score": 90, "historical_win_pct": 70, "rr": 2.5, "potential_pct": 40},
            {"coin": "B", "entry_quality": 60, "score": 70, "expansion_score": 60, "historical_win_pct": 55, "rr": 1.8, "potential_pct": 20},
        ]
        ranked = scanner._rank_trade_candidates(hits, cfg)
        self.assertEqual(ranked[0]["coin"], "A")
        self.assertGreater(ranked[0]["trade_quality"], ranked[1]["trade_quality"])


if __name__ == "__main__":
    unittest.main()
