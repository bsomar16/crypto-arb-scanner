import unittest

from oos_4h_exit_policy import resolve_exit


class ExitPolicyTests(unittest.TestCase):
    def test_stop_wins_when_stop_and_target_same_bar(self):
        rows = [
            [0, "10", "10", "10", "10", "0", "0"],
            [1, "10", "12", "9", "11", "0", "0"],
        ]
        signal = {"entry": 10.0, "stop": 9.0, "t1": 11.0, "t2": 12.0, "t3": 13.0}
        result = resolve_exit(rows, 0, signal, "TP1", 2)
        self.assertEqual(result["outcome"], "LOSS")

    def test_tp1_resolves_as_win_before_tp3(self):
        rows = [
            [0, "10", "10", "10", "10", "0", "0"],
            [1, "10", "11.2", "9.9", "11", "0", "0"],
        ]
        signal = {"entry": 10.0, "stop": 9.0, "t1": 11.0, "t2": 12.0, "t3": 13.0}
        result = resolve_exit(rows, 0, signal, "TP1", 2)
        self.assertEqual(result["outcome"], "WIN")
        self.assertEqual(result["exit_price"], 11.0)

if __name__ == "__main__":
    unittest.main()
