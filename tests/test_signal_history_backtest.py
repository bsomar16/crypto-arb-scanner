import json
import os
import tempfile
import unittest

import signal_history


class SignalHistoryBacktestTests(unittest.TestCase):
    def test_backtest_fallback_is_used_only_with_enough_sample(self):
        with tempfile.TemporaryDirectory() as td:
            old_path = signal_history.PATH
            old_bt = signal_history.BACKTEST_STATS_PATH
            signal_history.PATH = os.path.join(td, "history.jsonl")
            signal_history.BACKTEST_STATS_PATH = os.path.join(td, "backtest.json")
            try:
                with open(signal_history.BACKTEST_STATS_PATH, "w", encoding="utf-8") as f:
                    json.dump({"generated_at": "2026-09-16T00:00:00+00:00", "stats": {
                        "BTC|15m": {"wins": 14, "losses": 6, "win_pct": 70.0}
                    }}, f)
                signal = {"coin": "BTC", "interval": "15m", "setup_type": "BREAKOUT", "entry": 100}
                stats = signal_history.comparable_stats(signal, min_samples=20)
                self.assertEqual(stats["sample"], 20)
                self.assertEqual(stats["win_pct"], 70.0)
                self.assertEqual(stats["scope"], "backtest coin/timeframe")
            finally:
                signal_history.PATH = old_path
                signal_history.BACKTEST_STATS_PATH = old_bt

    def test_live_results_take_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            old_path = signal_history.PATH
            old_bt = signal_history.BACKTEST_STATS_PATH
            signal_history.PATH = os.path.join(td, "history.jsonl")
            signal_history.BACKTEST_STATS_PATH = os.path.join(td, "backtest.json")
            try:
                signal = {"coin": "BTC", "interval": "15m", "setup_type": "BREAKOUT", "entry": 100}
                for i in range(20):
                    s = dict(signal, entry=100 + i * 0.01)
                    signal_history.record_signal(s)
                    signal_history.record_outcome(s, "WIN" if i < 12 else "LOSS")
                with open(signal_history.BACKTEST_STATS_PATH, "w", encoding="utf-8") as f:
                    json.dump({"stats": {"BTC|15m": {"wins": 20, "losses": 0, "win_pct": 100.0}}}, f)
                stats = signal_history.comparable_stats(signal, min_samples=20)
                self.assertEqual(stats["sample"], 20)
                self.assertEqual(stats["win_pct"], 60.0)
                self.assertEqual(stats["scope"], "exact")
                self.assertEqual(stats["source"], "live")
            finally:
                signal_history.PATH = old_path
                signal_history.BACKTEST_STATS_PATH = old_bt


if __name__ == "__main__":
    unittest.main()
