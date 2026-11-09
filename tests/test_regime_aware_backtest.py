import unittest

from walk_forward import build_windows, summarize_by_regime


class RegimeAwareWalkForwardTests(unittest.TestCase):
    def test_regime_summary_is_separated(self):
        trades = [
            {"market_regime": "BULLISH", "outcome": "WIN", "mfe_pct": 8, "mae_pct": -1},
            {"market_regime": "BULLISH", "outcome": "LOSS", "mfe_pct": 3, "mae_pct": -2},
            {"market_regime": "BEARISH", "outcome": "LOSS", "mfe_pct": 1, "mae_pct": -3},
        ]
        report = summarize_by_regime(trades)
        self.assertEqual(report["BULLISH"]["signals"], 2)
        self.assertEqual(report["BULLISH"]["win_pct"], 50.0)
        self.assertEqual(report["BEARISH"]["losses"], 1)

    def test_step_cannot_create_overlapping_test_windows(self):
        rows = [[i] for i in range(20)]
        windows = build_windows(rows, train_bars=5, test_bars=3, step_bars=1, max_windows=3)
        self.assertEqual(windows[0]["test_end"], windows[1]["test_start"])
        self.assertEqual(windows[1]["test_end"], windows[2]["test_start"])

    def test_training_ends_before_regime_test_timestamp(self):
        rows = [[i] for i in range(12)]
        windows = build_windows(rows, train_bars=4, test_bars=2, step_bars=2, max_windows=2)
        for w in windows:
            self.assertLess(w["train_end_ts"], w["test_start_ts"])


if __name__ == "__main__":
    unittest.main()
