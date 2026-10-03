import json
import unittest

from research_4h_shadow_promotion import build_report, wilson_interval


class ShadowPromotionTests(unittest.TestCase):
    def test_wilson_interval_has_bounds(self):
        lo, hi = wilson_interval(18, 20)
        self.assertIsNotNone(lo)
        self.assertIsNotNone(hi)
        self.assertLessEqual(lo, hi)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 100.0)

    def test_insufficient_sample_blocks_research_eligibility(self):
        state = {
            "runs": 3,
            "last_run_at": "2026-10-03T10:00:00+00:00",
            "candidates": ["baseline", "pullback"],
            "summaries": {
                "baseline": {"tracked": 8, "closed_decisive": 8, "wins": 3, "losses": 5, "expired": 0, "precision_pct": 37.5},
                "pullback": {"tracked": 4, "closed_decisive": 4, "wins": 2, "losses": 2, "expired": 0, "precision_pct": 50.0},
            },
        }
        report = build_report(state)
        pullback = next(x for x in report["candidates"] if x["candidate"] == "pullback")
        self.assertEqual(pullback["status"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(pullback["sample_ok"])

    def test_candidate_below_baseline_is_not_eligible(self):
        state = {
            "runs": 10,
            "candidates": ["baseline", "pullback"],
            "summaries": {
                "baseline": {"closed_decisive": 20, "wins": 12, "losses": 8, "expired": 0, "precision_pct": 60.0},
                "pullback": {"closed_decisive": 20, "wins": 10, "losses": 10, "expired": 0, "precision_pct": 50.0},
            },
        }
        report = build_report(state)
        pullback = next(x for x in report["candidates"] if x["candidate"] == "pullback")
        self.assertEqual(pullback["status"], "BELOW_BASELINE")
        self.assertFalse(pullback["baseline_preserved"])

    def test_90_percent_target_is_reported_separately(self):
        state = {
            "runs": 20,
            "candidates": ["baseline", "candidate"],
            "summaries": {
                "baseline": {"closed_decisive": 20, "wins": 17, "losses": 3, "expired": 0, "precision_pct": 85.0},
                "candidate": {"closed_decisive": 20, "wins": 18, "losses": 2, "expired": 0, "precision_pct": 90.0},
            },
        }
        report = build_report(state)
        candidate = next(x for x in report["candidates"] if x["candidate"] == "candidate")
        self.assertEqual(candidate["status"], "RESEARCH_ELIGIBLE")
        self.assertTrue(candidate["target_90_met"])
        self.assertTrue(report["target_90_is_research_target_only"])


if __name__ == "__main__":
    unittest.main()
