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

    def test_sls_signal_does_not_repeat_after_pipe_key_migration(self):
        """Regression test for BTC|SLS vs BTC key mismatch that caused infinite SLS repeats."""
        sls_hit = {
            "coin": "BTC",
            "strategy": "SLS",
            "interval": "5m",
            "setup_type": "BNC",
            "entry": 64754.01,
            "score": 76.0,
            "rr": 2.5,
            "potential_pct": 8.0,
            "candle_open_time": 1794992400000,
        }
        # Previously stored under plain 'BTC' after migration
        fired = {
            "BTC": {
                "ts": 1794991939.0,
                "entry": 64780.01,
                "score": 73.0,
                "setup_type": "BNC",
                "interval": "5m",
                "candle_open_time": 1794991500000,
                "strategy": "SLS",
            }
        }
        cfg = {"sls_allow_multi_timeframe_signals": False, "buy_signal_cooldown_minutes": 180}
        fresh, updates = scanner._dedupe_buy_signals([sls_hit], fired, 1794992832.0, active_coins=set(), cfg=cfg)
        self.assertEqual(fresh, [])
        self.assertEqual(updates, {})

    def test_cooldown_suppresses_repeated_buy_signals_within_window(self):
        """Within 180m cooldown, same coin must not repeat even on score bump or interval change."""
        hit = self._signal(coin="XRP", interval="1h", setup="BREAKOUT", entry=1.02, score=95)
        fired = {
            "XRP": {
                "ts": 10_000.0,
                "entry": 0.98,
                "score": 80.0,
                "setup_type": "MOMENTUM",
                "interval": "15m",
                "candle_open_time": 1000,
            }
        }
        cfg = {"buy_signal_cooldown_minutes": 180}
        # 30 minutes later -> suppressed
        fresh, updates = scanner._dedupe_buy_signals([hit], fired, 10_000.0 + 1800.0, active_coins=set(), cfg=cfg)
        self.assertEqual(fresh, [])
        self.assertEqual(updates, {})

        # 3 hours + 1 second later -> allowed
        fresh_after, updates_after = scanner._dedupe_buy_signals(
            [hit], fired, 10_000.0 + 180 * 60 + 1, active_coins=set(), cfg=cfg
        )
        self.assertEqual(len(fresh_after), 1)
        self.assertIn("XRP", updates_after)

    def test_cooldown_allows_explicit_early_brk_to_brk_upgrade(self):
        hit = {
            "coin": "ETH",
            "strategy": "SLS",
            "interval": "15m",
            "setup_type": "BRK",
            "setup_upgrade_from": "EARLY_BRK",
            "entry": 1820.0,
            "score": 85.0,
            "rr": 2.8,
            "potential_pct": 10.0,
            "candle_open_time": 2000,
        }
        fired = {
            "ETH": {
                "ts": 10_000.0,
                "entry": 1815.0,
                "score": 78.0,
                "setup_type": "EARLY_BRK",
                "interval": "15m",
                "candle_open_time": 1000,
            }
        }
        cfg = {"sls_allow_multi_timeframe_signals": False, "buy_signal_cooldown_minutes": 180}
        fresh, updates = scanner._dedupe_buy_signals([hit], fired, 10_900.0, active_coins=set(), cfg=cfg)
        self.assertEqual(len(fresh), 1)
        self.assertIn("ETH", updates)

    def test_active_position_blocks_sls_when_mtf_repeats_disabled(self):
        sls_hit = {
            "coin": "SOL",
            "strategy": "SLS",
            "interval": "15m",
            "setup_type": "BRK",
            "entry": 75.0,
            "score": 90.0,
            "rr": 3.0,
            "potential_pct": 12.0,
        }
        cfg = {"sls_allow_multi_timeframe_signals": False, "buy_signal_cooldown_minutes": 180}
        fresh, updates = scanner._dedupe_buy_signals([sls_hit], {}, 10_000.0, active_coins={"SOL"}, cfg=cfg)
        self.assertEqual(fresh, [])
        self.assertEqual(updates, {})


if __name__ == "__main__":
    unittest.main()
