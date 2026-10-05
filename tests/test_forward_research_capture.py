import json
import tempfile
import unittest

import signal_history


class ForwardResearchCaptureTests(unittest.TestCase):  # research telemetry only
    def test_record_signal_persists_entry_time_features(self):
        with tempfile.TemporaryDirectory() as td:
            path = f"{td}/signals.jsonl"
            original = signal_history.PATH
            signal_history.PATH = path
            try:
                signal = {
                    "coin": "TEST",
                    "interval": "4h",
                    "setup_type": "PULLBACK",
                    "entry": 100.0,
                    "stop": 95.0,
                    "t1": 110.0,
                    "t2": 115.0,
                    "t3": 120.0,
                    "score": 72,
                    "rr": 2.0,
                    "potential_pct": 20,
                    "trend_4h": "MIXED",
                    "candle_open_time": 123456,
                    "expansion_state": "BASE",
                    "expansion_volume_ratio": 1.1,
                    "expansion_extension_pct": 1.5,
                    "entry_extension_pct": 1.2,
                    "entry_quality": 82,
                    "structure_score": 57,
                    "vol_x": 1.3,
                    "volatility": {"state": "EXPANDING"},
                    "order_block": {"bullish": True},
                    "zero_inverse": {"bullish_reclaim": True},
                    "bos_level": 99.0,
                    "entry_trigger": "BOS_RETEST_CONFIRM",
                }
                signal_history.record_signal(signal, source="forward_research")
                row = json.loads(open(path, encoding="utf-8").readline())
                self.assertEqual(row["source"], "forward_research")
                self.assertEqual(row["structure_score"], 57)
                self.assertTrue(row["order_block_bullish"])
                self.assertTrue(row["zero_inverse_bullish"])
                self.assertEqual(row["expansion_state"], "BASE")
                self.assertEqual(row["entry_extension_pct"], 1.2)
            finally:
                signal_history.PATH = original


if __name__ == "__main__":
    unittest.main()
