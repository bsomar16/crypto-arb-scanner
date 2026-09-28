import unittest

from validation import summarize_near_miss_shadows, _merge_shadow_summaries


class NearMissShadowValidationTests(unittest.TestCase):
    def test_shadow_summary_is_stage_specific(self):
        rows = [
            {"stage": "liquidity", "mfe_pct": 12, "mae_pct": -2, "ret_pct": 5,
             "milestones": {"5": True, "10": True, "20": False, "30": False}, "censored": False},
            {"stage": "liquidity", "mfe_pct": 8, "mae_pct": -3, "ret_pct": 2,
             "milestones": {"5": True, "10": False, "20": False, "30": False}, "censored": False},
            {"stage": "volume", "mfe_pct": 4, "mae_pct": -1, "ret_pct": 1,
             "milestones": {"5": False, "10": False, "20": False, "30": False}, "censored": False},
        ]
        out = summarize_near_miss_shadows(rows)
        self.assertEqual(out["liquidity"]["samples"], 2)
        self.assertEqual(out["liquidity"]["hit_5_pct"], 100.0)
        self.assertEqual(out["volume"]["samples"], 1)
        self.assertEqual(out["volume"]["hit_5_pct"], 0.0)

    def test_censored_rows_are_excluded(self):
        rows = [{"stage": "entry_confirmation", "mfe_pct": 50, "mae_pct": 0, "ret_pct": 20,
                 "milestones": {"5": True}, "censored": True}]
        self.assertEqual(summarize_near_miss_shadows(rows), {})

    def test_merge_preserves_weighted_samples(self):
        out = _merge_shadow_summaries([
            {"liquidity": {"samples": 2, "mfe_avg_pct": 10, "mae_avg_pct": -2, "avg_return_pct": 4,
                           "hit_5_pct": 100, "hit_10_pct": 50, "hit_20_pct": 0, "hit_30_pct": 0}},
            {"liquidity": {"samples": 1, "mfe_avg_pct": 4, "mae_avg_pct": -1, "avg_return_pct": 1,
                           "hit_5_pct": 0, "hit_10_pct": 0, "hit_20_pct": 0, "hit_30_pct": 0}},
        ])
        self.assertEqual(out["liquidity"]["samples"], 3)
        self.assertAlmostEqual(out["liquidity"]["mfe_avg_pct"], 8.0, places=2)
        self.assertAlmostEqual(out["liquidity"]["hit_5_pct"], 66.67, places=2)


if __name__ == "__main__":
    unittest.main()
