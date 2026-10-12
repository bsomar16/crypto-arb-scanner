import unittest

from oos_4h_early_retest_policy import evaluate_window_deltas


def window(name, baseline_precision, candidate_precision, baseline_closed=10, candidate_closed=10):
    return {
        "window": name,
        "variants": {
            "0.42": {"policies": {"TP3": {
                "closed_decisive": baseline_closed,
                "precision_pct": baseline_precision,
            }}},
            "0.40": {"policies": {"TP3": {
                "closed_decisive": candidate_closed,
                "precision_pct": candidate_precision,
            }}},
        },
    }


class EarlyRetestEligibilityTests(unittest.TestCase):
    def test_candidate_must_have_results_in_every_informative_baseline_window(self):
        windows = [
            window("oos_1", 50.0, 60.0),
            window("oos_2", 40.0, None, candidate_closed=0),
        ]
        result = evaluate_window_deltas(windows, "0.40")
        self.assertFalse(result["passes"])
        self.assertEqual(result["informative_windows"], ["oos_1", "oos_2"])
        self.assertEqual(result["evaluated_windows"], 1)

    def test_negative_delta_in_any_window_fails(self):
        windows = [
            window("oos_1", 50.0, 60.0),
            window("oos_2", 40.0, 30.0),
        ]
        result = evaluate_window_deltas(windows, "0.40")
        self.assertFalse(result["passes"])
        self.assertEqual([x["delta_precision_pp"] for x in result["deltas"]], [10.0, -10.0])

    def test_nonnegative_delta_in_every_informative_window_passes(self):
        windows = [
            window("oos_1", 50.0, 50.0),
            window("oos_2", 40.0, 45.0),
            window("oos_3", None, 70.0, baseline_closed=0),
        ]
        result = evaluate_window_deltas(windows, "0.40")
        self.assertTrue(result["passes"])
        self.assertEqual(result["informative_windows"], ["oos_1", "oos_2"])
        self.assertEqual(result["evaluated_windows"], 2)


if __name__ == "__main__":
    unittest.main()
