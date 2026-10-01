import unittest

from threshold_sensitivity import ENTRY_POLICIES, compare_variant


class ThresholdSensitivityTests(unittest.TestCase):
    def test_entry_policies_are_explicit(self):
        self.assertTrue(ENTRY_POLICIES["current"]["require_retest"])
        self.assertFalse(ENTRY_POLICIES["current"]["require_sweep"])
        self.assertFalse(ENTRY_POLICIES["no_early_retest"]["allow_early_retest"])
        self.assertTrue(ENTRY_POLICIES["sweep_required"]["require_sweep"])
        self.assertFalse(ENTRY_POLICIES["retest_optional"]["require_retest"])

    def test_variant_comparison_reports_precision_and_delta_inputs(self):
        out = compare_variant("baseline", [
            {"summary": {"signals": 10, "closed": 10, "wins": 8},
             "audit": {"qualified": 8, "liquidity": 2}}
        ])
        self.assertEqual(out["detected_signals"], 10)
        self.assertEqual(out["closed"], 10)
        self.assertEqual(out["wins"], 8)
        self.assertEqual(out["precision_pct"], 80.0)
        self.assertEqual(out["rejected"], 2)
        self.assertIn("unknown", out["by_interval"])
        self.assertEqual(out["by_interval"]["unknown"]["precision_pct"], 80.0)


if __name__ == "__main__":
    unittest.main()
