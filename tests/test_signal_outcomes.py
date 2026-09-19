import unittest

import signal_outcomes


class SignalOutcomeTests(unittest.TestCase):
    def _signal(self):
        return {
            "coin": "AAA",
            "interval": "15m",
            "entry": 100.0,
            "stop": 95.0,
            "target": 130.0,
            "candle_open_time": 1000,
        }

    def test_stop_wins_same_candle_against_milestone(self):
        rows = [
            [1000, 100, 101, 99, 100, 1],
            [2000, 100, 106, 94, 99, 1],
        ]
        result = signal_outcomes._evaluate(self._signal(), rows, 1)
        self.assertEqual(result["outcome"], "LOSS")
        self.assertFalse(result["milestones"]["5"])
        self.assertLessEqual(result["mae_pct"], -5)

    def test_mfe_mae_and_milestones_are_recorded_before_win(self):
        rows = [
            [1000, 100, 101, 99, 100, 1],
            [2000, 100, 111, 98, 109, 1],
            [3000, 109, 131, 105, 130, 1],
        ]
        result = signal_outcomes._evaluate(self._signal(), rows, 2)
        self.assertEqual(result["outcome"], "WIN")
        self.assertTrue(result["milestones"]["5"])
        self.assertTrue(result["milestones"]["10"])
        self.assertTrue(result["milestones"]["20"])
        self.assertGreaterEqual(result["mfe_pct"], 30)
        self.assertLess(result["mae_pct"], 0)

    def test_incomplete_horizon_stays_pending(self):
        rows = [
            [1000, 100, 101, 99, 100, 1],
            [2000, 100, 120, 99, 115, 1],
        ]
        result = signal_outcomes._evaluate(self._signal(), rows, 3)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
