import unittest
from live_replay import evaluate_outcome, _bars_for_hold
from signals import TP_POTENTIAL_CEILINGS

class LiveReplayTests(unittest.TestCase):
    def test_summarize_reports_conservative_all_outcome_win_rate_and_returns(self):
        from live_replay import summarize

        rows = [
            {"outcome": "WIN", "ret_pct": 4.0, "milestones": {"5": True}, "potential_pct": 5, "rr": 2},
            {"outcome": "LOSS", "ret_pct": -2.0, "milestones": {}, "potential_pct": 5, "rr": 2},
            {"outcome": "EXPIRED", "ret_pct": 1.0, "milestones": {}, "potential_pct": 5, "rr": 2},
            {"outcome": "EXPIRED", "ret_pct": 99.0, "censored": True, "milestones": {}, "potential_pct": 5, "rr": 2},
        ]
        result = summarize(rows)
        self.assertEqual(result["signals"], 4)
        self.assertEqual(result["evaluated"], 3)
        self.assertEqual(result["closed"], 2)
        self.assertEqual(result["precision_pct"], 50.0)
        self.assertAlmostEqual(result["win_rate_all_outcomes_pct"], 100 / 3)
        self.assertAlmostEqual(result["avg_return_pct_gross"], 1.0)
        self.assertAlmostEqual(result["profit_factor_gross"], 2.5)
        self.assertEqual(result["censored"], 1)

    def test_tp1_tp2_tp3_hit_rates_are_reported_for_eligible_evaluated_trades(self):
        from live_replay import summarize

        rows = [
            {"outcome": "WIN", "ret_pct": 8.0,
             "target_eligible": {"tp1": True, "tp2": True, "tp3": True},
             "target_hits": {"tp1": True, "tp2": True, "tp3": True}},
            {"outcome": "LOSS", "ret_pct": -3.0,
             "target_eligible": {"tp1": True, "tp2": True, "tp3": True},
             "target_hits": {"tp1": True, "tp2": False, "tp3": False}},
            {"outcome": "EXPIRED", "ret_pct": 1.0, "censored": True,
             "target_eligible": {"tp1": True, "tp2": True, "tp3": True},
             "target_hits": {"tp1": True, "tp2": True, "tp3": False}},
        ]
        result = summarize(rows)
        self.assertEqual(result["target_hits"]["tp1"]["hits"], 2)
        self.assertEqual(result["target_hits"]["tp1"]["eligible"], 2)
        self.assertEqual(result["target_hits"]["tp1"]["rate_pct"], 100.0)
        self.assertEqual(result["target_hits"]["tp2"]["rate_pct"], 50.0)
        self.assertEqual(result["target_hits"]["tp3"]["rate_pct"], 50.0)

    def test_replay_tracks_each_target_without_crediting_stop_candle(self):
        rows = [
            [0, 1, 1, 1, 1, 0, 0, 0],
            [1, 1, 1.06, 0.99, 1.04, 0, 0, 0],
            [2, 1.04, 1.16, 1.01, 1.14, 0, 0, 0],
        ]
        signal = {"entry": 1.0, "stop": 0.9, "t1": 1.05, "t2": 1.10, "t3": 1.15}
        result = evaluate_outcome(rows, 0, signal, 2)
        self.assertTrue(result["target_hits"]["tp1"])
        self.assertTrue(result["target_hits"]["tp2"])
        self.assertFalse(result["target_hits"]["tp3"])

    def test_same_candle_stop_does_not_credit_target_hit(self):
        rows = [
            [0, 1, 1, 1, 1, 0, 0, 0],
            [1, 1, 1.2, 0.8, 1.0, 0, 0, 0],
        ]
        signal = {"entry": 1.0, "stop": 0.9, "t1": 1.05, "t2": 1.1, "t3": 1.15}
        result = evaluate_outcome(rows, 0, signal, 1)
        self.assertEqual(result["outcome"], "LOSS")
        self.assertFalse(result["target_hits"]["tp1"])
        self.assertFalse(result["target_hits"]["tp2"])
        self.assertFalse(result["target_hits"]["tp3"])

    def test_outcome_returns_are_recorded_for_win_and_loss(self):
        win_rows = [[0,1,1,1,1,0,0,0], [1,1,1.2,0.99,1.15,0,0,0]]
        loss_rows = [[0,1,1,1,1,0,0,0], [1,1,1.02,0.8,0.85,0,0,0]]
        win = evaluate_outcome(win_rows, 0, {"entry":1.0,"stop":0.9,"t3":1.1}, 1)
        loss = evaluate_outcome(loss_rows, 0, {"entry":1.0,"stop":0.9,"t3":1.1}, 1)
        self.assertAlmostEqual(win["ret_pct"], 10.0)
        self.assertAlmostEqual(loss["ret_pct"], -10.0)

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

    def test_early_win_is_retained_as_complete(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.2,0.99,1.15,0,0,0],
            [2,1.1,1.15,1.0,1.1,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.10}
        result=evaluate_outcome(rows,0,signal,2)
        self.assertEqual(result["outcome"],"WIN")
        self.assertTrue(result["window_complete"])
        self.assertFalse(result["censored"])
        self.assertEqual(result["exit_i"],1)

    def test_early_loss_is_retained_as_complete(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.02,0.8,0.85,0,0,0],
            [2,0.9,1.1,0.85,1.05,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.10}
        result=evaluate_outcome(rows,0,signal,2)
        self.assertEqual(result["outcome"],"LOSS")
        self.assertTrue(result["window_complete"])
        self.assertFalse(result["censored"])
        self.assertEqual(result["exit_i"],1)

    def test_incomplete_expiry_is_censored(self):
        rows = [
            [0,1,1,1,1,0,0,0],
            [1,1,1.05,0.99,1.04,0,0,0],
            [2,1,1.2,0.99,1.1,0,0,0],
        ]
        signal={"entry":1.0,"stop":0.9,"t3":1.3}
        result=evaluate_outcome(rows,0,signal,2,max_index=2)
        self.assertEqual(result["outcome"],"EXPIRED")
        self.assertEqual(result["exit_i"],1)
        self.assertFalse(result["window_complete"])
        self.assertTrue(result["censored"])

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
