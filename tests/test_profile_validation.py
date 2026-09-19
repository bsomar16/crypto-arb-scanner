import unittest
from validation import summarize

class ProfileValidationTests(unittest.TestCase):
    def test_summarize_counts_closed_precision_and_milestone(self):
        rows = [
            {"outcome": "WIN", "milestones": {"5": True}, "potential_pct": 10, "rr": 2},
            {"outcome": "LOSS", "milestones": {"5": False}, "potential_pct": 8, "rr": 1.8},
            {"outcome": "EXPIRED", "milestones": {"5": True}, "potential_pct": 12, "rr": 2.2},
        ]
        s = summarize(rows)
        self.assertEqual(s["signals"], 3)
        self.assertEqual(s["closed"], 2)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["expired"], 1)
        self.assertEqual(s["precision_pct"], 50.0)
        self.assertAlmostEqual(s["milestone_5_pct"], 66.6666666667, places=6)

if __name__ == "__main__":
    unittest.main()
