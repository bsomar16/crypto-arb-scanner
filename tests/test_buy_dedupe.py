import unittest

from scanner import _buy_signal_identity, _dedupe_buy_signals


def signal(coin="TOMO", entry=1.0, score=60.0, setup="BREAKOUT", interval="5m",
           candle=100, entry_trigger="BOS_RETEST_CONFIRM", bos_level=0.99):
    return {
        "coin": coin, "entry": entry, "score": score, "rr": 2.0,
        "potential_pct": 20.0, "setup_type": setup, "interval": interval,
        "entry_trigger": entry_trigger, "candle_open_time": candle, "bos_level": bos_level,
    }


class BuyDedupeTests(unittest.TestCase):
    def test_signal_identity_ignores_volatile_price_and_candle_fields(self):
        a = signal(entry=1.0, score=60, candle=100)
        b = signal(entry=1.04, score=72, candle=500)
        self.assertEqual(_buy_signal_identity(a), _buy_signal_identity(b))

    def test_signal_identity_changes_on_new_bos_anchor(self):
        a = signal(bos_level=0.99)
        b = signal(bos_level=1.02)
        self.assertNotEqual(_buy_signal_identity(a), _buy_signal_identity(b))
    def test_same_tomo_setup_on_new_candle_is_suppressed(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(score=66, candle=200)], old, 1100,
            cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(fresh, [])

    def test_timeframe_change_alone_is_suppressed(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(interval="15m", candle=200)], old, 2000,
            cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(fresh, [])

    def test_material_entry_change_after_cooldown_is_suppressed(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "entry_trigger": "BOS_RETEST_CONFIRM",
                        "bos_level": 0.99, "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(entry=1.03, score=71, candle=400)], old, 1000 + 241 * 60,
            cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(fresh, [])

    def test_new_bos_anchor_after_cooldown_rearms(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "entry_trigger": "BOS_RETEST_CONFIRM",
                        "bos_level": 0.99, "candle_open_time": 100}}
        fresh, updates = _dedupe_buy_signals(
            [signal(entry=1.03, score=71, candle=400, bos_level=1.02)],
            old, 1000 + 241 * 60, cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(len(fresh), 1)
        self.assertIn("TOMO", updates)

    def test_price_drift_alone_after_cooldown_is_suppressed(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "entry_trigger": "BOS_RETEST_CONFIRM", "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(entry=1.03, score=61, candle=400)], old, 1000 + 241 * 60,
            cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(fresh, [])

    def test_new_entry_trigger_after_cooldown_rearms(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "entry_trigger": "BOS_RETEST_CONFIRM", "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(entry=1.005, score=61, candle=400,
                    entry_trigger="SWEEP_RECLAIM_BOS_RETEST_CONFIRM")],
            old, 1000 + 241 * 60, cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(len(fresh), 1)

    def test_material_setup_change_after_cooldown_rearms(self):
        old = {"TOMO": {"ts": 1000, "entry": 1.0, "score": 60, "setup_type": "BREAKOUT",
                        "interval": "5m", "candle_open_time": 100}}
        fresh, _ = _dedupe_buy_signals(
            [signal(setup="REVERSAL", score=71, candle=400)], old, 1000 + 241 * 60,
            cooldown_minutes=240, rearm_score_delta=10,
        )
        self.assertEqual(len(fresh), 1)


if __name__ == "__main__":
    unittest.main()
