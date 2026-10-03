import unittest

from oos_4h_five_window_tp1_holdout import CANDIDATES, WINDOWS, POLICY


class FiveWindowStudyTests(unittest.TestCase):
    def test_windows_are_five_predeclared_segments(self):
        self.assertEqual(len(WINDOWS), 5)
        self.assertEqual(WINDOWS[0], ("oos_1", 500, 1000))
        self.assertEqual(WINDOWS[-1], ("oos_5", 2500, 3000))

    def test_policy_and_candidates_are_frozen(self):
        self.assertEqual(POLICY, "TP1")
        self.assertEqual(
            set(CANDIDATES),
            {
                "baseline",
                "structure_ge_50",
                "base_and_structure_ge_50",
                "non_expansion_and_structure_ge_50",
            },
        )


if __name__ == "__main__":
    unittest.main()
