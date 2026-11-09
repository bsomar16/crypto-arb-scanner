import unittest

from discovery import _early_move_score


class EarlyMoveDiscoveryTests(unittest.TestCase):
    def test_positive_micro_acceleration_raises_score(self):
        base = _early_move_score(0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0)
        early = _early_move_score(2.0, 4.0, 1.4, 1.5, 2.0, 5.0, 0.4)
        self.assertGreater(early, base)

    def test_score_is_bounded(self):
        score = _early_move_score(20, 40, 20, 20, 20, 50, 2)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_micro_signal_does_not_depend_on_large_24h_move(self):
        score = _early_move_score(1.0, 1.5, 1.2, 1.2, 1.8, 2.0, 0.2)
        self.assertGreater(score, 0)
        self.assertLess(score, 100)


if __name__ == "__main__":
    unittest.main()
