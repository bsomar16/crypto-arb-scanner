import unittest

from abt_oos import _stats


class ABTOOSStatsTests(unittest.TestCase):
    def test_stage_stats_report_closed_win_rate_and_mfe_mae(self):
        rows = [
            {"outcome": "WIN", "ret_pct": 7.0, "mfe_pct": 9.0, "mae_pct": -1.0,
             "potential_pct": 10.0, "rr": 2.0,
             "milestones": {"5": True, "10": False, "20": False, "30": False, "50": False, "80": False}},
            {"outcome": "LOSS", "ret_pct": -2.0, "mfe_pct": 3.0, "mae_pct": -4.0,
             "potential_pct": 8.0, "rr": 1.8,
             "milestones": {"5": False, "10": False, "20": False, "30": False, "50": False, "80": False}},
            {"outcome": "EXPIRED", "ret_pct": 1.0, "mfe_pct": 4.0, "mae_pct": -2.0,
             "potential_pct": 9.0, "rr": 1.9,
             "milestones": {"5": False, "10": False, "20": False, "30": False, "50": False, "80": False}},
        ]
        s = _stats(rows)
        self.assertEqual(s["signals"], 3)
        self.assertEqual(s["closed"], 2)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["expired"], 1)
        self.assertEqual(s["win_rate_pct"], 50.0)
        self.assertAlmostEqual(s["avg_mfe_pct"], 16 / 3, places=6)
        self.assertAlmostEqual(s["avg_mae_pct"], -7 / 3, places=6)

    def test_empty_stage_is_safe(self):
        s = _stats([])
        self.assertEqual(s["signals"], 0)
        self.assertIsNone(s["win_rate_pct"])


if __name__ == "__main__":
    unittest.main()
