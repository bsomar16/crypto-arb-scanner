import unittest

import scanner


class BuySignalDedupeTests(unittest.TestCase):
    def _signal(self, coin="GALA", interval="15m", setup="MOMENTUM", entry=0.00183, score=73):
        return {
            "coin": coin,
            "interval": interval,
            "setup_type": setup,
            "entry": entry,
            "score": float(score),
            "rr": 2.6,
            "potential_pct": 6.6,
        }

    def test_same_coin_collapses_to_one_best_signal(self):
        hits = [
            self._signal(interval="5m", score=70),
            self._signal(interval="15m", score=82),
            self._signal(interval="1h", score=75),
        ]
        fresh, updates = scanner._dedupe_buy_signals(hits, {}, 1000, active_coins=set())
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["interval"], "15m")
        self.assertIn("GALA", updates)

    def test_previous_signal_is_not_repeated(self):
        hits = [self._signal()]
        fired = {
            "GALA": {
                "ts": 900,
                "entry": 0.00183,
                "setup_type": "MOMENTUM",
                "interval": "15m",
            }
        }
        fresh, updates = scanner._dedupe_buy_signals(hits, fired, 1000, active_coins=set())
        self.assertEqual(fresh, [])
        self.assertEqual(updates, {})

    def test_material_entry_change_is_a_new_signal(self):
        hits = [self._signal(entry=0.00190)]
        fired = {
            "GALA": {
                "ts": 900,
                "entry": 0.00183,
                "setup_type": "MOMENTUM",
                "interval": "15m",
            }
        }
        fresh, updates = scanner._dedupe_buy_signals(hits, fired, 1000, active_coins=set())
        self.assertEqual(len(fresh), 1)
        self.assertIn("GALA", updates)

    def test_setup_change_is_a_new_signal(self):
        hits = [self._signal(setup="BREAKOUT")]
        fired = {
            "GALA": {
                "ts": 900,
                "entry": 0.00183,
                "setup_type": "MOMENTUM",
                "interval": "15m",
            }
        }
        fresh, _ = scanner._dedupe_buy_signals(hits, fired, 1000, active_coins=set())
        self.assertEqual(len(fresh), 1)

    def test_active_coin_never_repeats_even_if_setup_changes(self):
        hits = [self._signal(setup="BREAKOUT", entry=0.00195, interval="5m")]
        fired = {
            "GALA": {
                "ts": 900,
                "entry": 0.00183,
                "setup_type": "MOMENTUM",
                "interval": "15m",
            }
        }
        fresh, updates = scanner._dedupe_buy_signals(hits, fired, 1000, active_coins={"GALA"})
        self.assertEqual(fresh, [])
        self.assertEqual(updates, {})

    def test_closed_coin_can_alert_on_new_setup(self):
        hits = [self._signal(setup="BREAKOUT")]
        fired = {
            "GALA": {
                "ts": 900,
                "entry": 0.00183,
                "setup_type": "MOMENTUM",
                "interval": "15m",
            }
        }
        fresh, updates = scanner._dedupe_buy_signals(hits, fired, 1000, active_coins=set())
        self.assertEqual(len(fresh), 1)
        self.assertIn("GALA", updates)


if __name__ == "__main__":
    unittest.main()
