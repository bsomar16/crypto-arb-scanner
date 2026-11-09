import unittest

from scanner import _dedupe_buy_signals
from signal_audit import SignalAudit

def signal(coin="GALA", interval="15m", entry=1.0, score=70.0, candle=1000, setup="MOMENTUM"):
    return {
        "coin": coin,
        "interval": interval,
        "entry": entry,
        "score": score,
        "rr": 2.0,
        "potential_pct": 10.0,
        "setup_type": setup,
        "candle_open_time": candle,
    }

class SignalDedupeTests(unittest.TestCase):
    def test_newer_candle_with_strengthened_score_can_realert(self):
        audit = SignalAudit()
        fresh, updates = _dedupe_buy_signals(
            [signal(candle=2000, score=76)],
            {"GALA": {"entry": 1.0, "score": 70, "setup_type": "MOMENTUM", "interval": "15m", "candle_open_time": 1000}},
            123,
            audit=audit,
        )
        self.assertEqual(len(fresh), 1)
        self.assertEqual(updates["GALA"]["candle_open_time"], 2000)
        self.assertNotIn("dedupe_unchanged", audit.snapshot())

    def test_unchanged_setup_is_deduped(self):
        audit = SignalAudit()
        fresh, _ = _dedupe_buy_signals(
            [signal(candle=2000, score=72)],
            {"GALA": {"entry": 1.0, "score": 70, "setup_type": "MOMENTUM", "interval": "15m", "candle_open_time": 1000}},
            123,
            audit=audit,
        )
        self.assertEqual(fresh, [])
        self.assertEqual(audit.snapshot()["dedupe_unchanged"], 1)

    def test_open_position_is_always_deduped(self):
        audit = SignalAudit()
        fresh, _ = _dedupe_buy_signals(
            [signal(candle=2000, score=90)],
            {},
            123,
            active_coins={"GALA"},
            audit=audit,
        )
        self.assertEqual(fresh, [])
        self.assertEqual(audit.snapshot()["dedupe_active_position"], 1)

if __name__ == "__main__":
    unittest.main()
