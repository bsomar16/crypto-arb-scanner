import json
import os
import tempfile
import unittest
from unittest.mock import patch

import position_state


class PositionStateTests(unittest.TestCase):
    def test_migrates_legacy_position_with_stable_id(self):
        legacy = [{
            "coin": "BTC",
            "entry": 100000,
            "entry_ts": "2026-09-17T12:00:00+00:00",
            "sl": 95000,
            "tp1": 105000,
            "tp2": 110000,
            "tp3": 120000,
            "status": "open",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "positions.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(legacy, fh)
            with patch.object(position_state, "POS_FILE", path):
                self.assertEqual(position_state.migrate_positions(), 1)
                with open(path, "r", encoding="utf-8") as fh:
                    first = json.load(fh)
                self.assertTrue(first[0]["position_id"].startswith("legacy-"))
                self.assertEqual(first[0]["mode"], "paper")
                self.assertEqual(first[0]["exchange"], "BINANCE")

                self.assertEqual(position_state.migrate_positions(), 0)
                with open(path, "r", encoding="utf-8") as fh:
                    second = json.load(fh)
                self.assertEqual(first[0]["position_id"], second[0]["position_id"])


if __name__ == "__main__":
    unittest.main()
