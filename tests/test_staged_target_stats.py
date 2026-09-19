import unittest

import signal_history


class StagedTargetStatsTests(unittest.TestCase):
    def test_requires_mature_live_sample_and_excludes_legacy_rows(self):
        original = signal_history._read
        try:
            def fake_read():
                rows = []
                for i in range(30):
                    rows.append({
                        "kind": "outcome",
                        "outcome": "WIN" if i < 18 else "LOSS",
                        "outcome_source": "live",
                        "interval": "15m",
                        "setup_type": "MOMENTUM",
                        "staged_targets": {"t1": 105, "t2": 110, "t3": 120},
                        "staged_target_hits": {"t1": True, "t2": i < 20, "t3": i < 10},
                    })
                rows.append({
                    "kind": "outcome", "outcome": "WIN", "interval": "15m",
                    "setup_type": "MOMENTUM",
                    "staged_targets": {"t1": 105, "t2": 110, "t3": 120},
                    "staged_target_hits": {"t1": False, "t2": False, "t3": False},
                })
                rows.append({
                    "kind": "outcome", "outcome": "WIN", "outcome_source": "backtest",
                    "interval": "15m", "setup_type": "MOMENTUM",
                    "staged_targets": {"t1": 105, "t2": 110, "t3": 120},
                    "staged_target_hits": {"t1": False, "t2": False, "t3": False},
                })
                return rows
            signal_history._read = fake_read
            result = signal_history.staged_target_stats(min_samples=30)
            self.assertTrue(result["mature"])
            self.assertEqual(result["overall"]["sample"], 30)
            self.assertEqual(result["overall"]["hit_rates_pct"]["t1"], 100.0)
            self.assertAlmostEqual(result["overall"]["hit_rates_pct"]["t2"], 66.67, places=2)
            self.assertAlmostEqual(result["overall"]["hit_rates_pct"]["t3"], 33.33, places=2)
        finally:
            signal_history._read = original

    def test_sparse_sample_is_not_exposed_as_mature(self):
        original = signal_history._read
        try:
            signal_history._read = lambda: [{
                "kind": "outcome", "outcome": "WIN", "outcome_source": "live",
                "staged_targets": {"t1": 105, "t2": 110, "t3": 120},
                "staged_target_hits": {"t1": True, "t2": True, "t3": True},
            }]
            result = signal_history.staged_target_stats(min_samples=30)
            self.assertFalse(result["mature"])
            self.assertIsNone(result["overall"])
        finally:
            signal_history._read = original


if __name__ == "__main__":
    unittest.main()
