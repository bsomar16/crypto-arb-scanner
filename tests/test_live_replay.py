import unittest
from live_replay import evaluate_outcome, _bars_for_hold
from signals import TP_POTENTIAL_CEILINGS

class LiveReplayTests(unittest.TestCase):
    def test_all_horizon_caps_exist(self):
        self.assertEqual(set(TP_POTENTIAL_CEILINGS), {"5m","15m","1h","4h","1d","1w"})

    def test_hold_conversion_is_positive(self):
        for interval in TP_POTENTIAL_CEILINGS:
            self.assertGreater(_bars_for_hold(interval, 24), 0)

    def test_same_candle_stop_is_conservative(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.2,0.8,1.1,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.1}
        result=evaluate_outcome(rows,0,signal,1)
        self.assertEqual(result["outcome"],"LOSS")

    def test_outcome_respects_oos_boundary(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.05,0.99,1.04,0,0,0],
            [2,1,1.20,0.99,1.10,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.10}
        result=evaluate_outcome(rows,0,signal,2,max_index=2)
        self.assertEqual(result["outcome"],"EXPIRED")
        self.assertEqual(result["exit_i"],1)

    def test_milestone_is_recorded_before_target(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.06,0.99,1.05,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.10}
        result=evaluate_outcome(rows,0,signal,1)
        self.assertEqual(result["outcome"],"EXPIRED")
        self.assertTrue(result["milestones"]["5"])

if __name__ == "__main__":
    unittest.main()
