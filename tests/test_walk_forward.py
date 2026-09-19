import tempfile
import unittest

from walk_forward import build_windows, evaluate_windows, persist


class WalkForwardTests(unittest.TestCase):
    def rows(self, n=20):
        return [[i, 0, 10, 9, 10, 100] for i in range(n)]

    def test_windows_are_strictly_chronological_and_non_leaking(self):
        rows = self.rows()
        windows = build_windows(rows, train_bars=6, test_bars=3, step_bars=3, max_windows=3)
        self.assertEqual(len(windows), 3)
        for w in windows:
            self.assertLess(w["train_end"], w["test_end"])
            self.assertLess(w["train_end_ts"], w["test_end_ts"])
            self.assertLess(w["train_end"], w["test_start"])
        self.assertEqual(windows[0]["test_end"], windows[1]["test_start"])

    def test_signal_can_see_history_but_training_ends_before_test(self):
        rows = self.rows()
        seen = []
        def signal_fn(i, train, all_rows):
            seen.append((i, train[-1][0], all_rows[i][0]))
            self.assertLess(train[-1][0], all_rows[i][0])
            return {"entry": 10}
        def outcome_fn(i, signal, all_rows):
            return {"outcome": "WIN", "mfe_pct": 5, "mae_pct": -1, "milestones": {"5": True}}
        report = evaluate_windows(rows, signal_fn, outcome_fn,
                                  train_bars=5, test_bars=2, step_bars=2, max_windows=2)
        self.assertEqual(report["outcome_source"], "walk_forward")
        self.assertTrue(seen)

    def test_future_test_rows_never_enter_training(self):
        rows = self.rows()
        def signal_fn(i, train, all_rows):
            self.assertTrue(all(r[0] < all_rows[i][0] for r in train))
            return None
        report = evaluate_windows(rows, signal_fn, lambda *args: None,
                                  train_bars=5, test_bars=2, step_bars=2, max_windows=3)
        self.assertEqual(report["summary"]["signals"], 0)

    def test_backtest_source_is_not_relabelled_as_live(self):
        rows = self.rows(10)
        def signal_fn(i, train, all_rows):
            return {"entry": 10}
        def outcome_fn(i, signal, all_rows):
            return {"outcome": "LOSS", "outcome_source": "backtest"}
        report = evaluate_windows(rows, signal_fn, outcome_fn,
                                  train_bars=4, test_bars=2, step_bars=2, max_windows=1)
        self.assertEqual(report["windows"][0]["trades"][0]["outcome_source"], "walk_forward")
        self.assertEqual(report["outcome_source"], "walk_forward")

    def test_persisted_report_is_reusable(self):
        rows = self.rows(12)
        report = evaluate_windows(rows, lambda i, train, all_rows: None,
                                  lambda *args: None, train_bars=4, test_bars=2)
        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            persist(report, f.name)
            f.seek(0)
            self.assertIn('"walk_forward"', f.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
