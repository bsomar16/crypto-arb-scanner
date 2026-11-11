"""Unit tests for SLS position state machine and persistent store."""
import os
import unittest
from unittest.mock import patch

from sls_position import SlsPositionManager
import sls_position_store


class SlsPositionTests(unittest.TestCase):
    def test_open_position_sets_five_targets(self):
        pm = SlsPositionManager()
        opened = pm.open(entry=100.0, stop=98.0, targets=[103.0, 105.0, 108.0, 112.0, 116.0],
                         bar_index=10, lifetime_bars=120)
        self.assertTrue(opened)
        self.assertTrue(pm.in_position)
        self.assertEqual(len(pm.position.targets), 5)

    def test_stop_loss_priority_over_target_touch(self):
        pm = SlsPositionManager()
        pm.open(entry=100.0, stop=95.0, targets=[105.0, 110.0, 115.0, 120.0, 125.0],
                bar_index=10, lifetime_bars=120)
        # Candle touches both low <= 95 and high >= 105
        events = pm.on_confirmed_bar(open_p=100.0, high_p=106.0, low_p=94.0, close_p=99.0, bar_index=11)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "STOP")
        self.assertFalse(pm.in_position)

    def test_trailing_stop_moves_to_breakeven_after_tp1(self):
        pm = SlsPositionManager()
        pm.open(entry=100.0, stop=95.0, targets=[105.0, 110.0, 115.0, 120.0, 125.0],
                bar_index=10, lifetime_bars=120)
        events = pm.on_confirmed_bar(open_p=100.0, high_p=106.0, low_p=98.0, close_p=105.0, bar_index=11)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "TP1")
        # SL should now be moved to entry (100.0)
        self.assertEqual(pm.position.stop, 100.0)

    def test_trailing_stop_moves_to_tp1_after_tp2(self):
        pm = SlsPositionManager()
        pm.open(entry=100.0, stop=95.0, targets=[105.0, 110.0, 115.0, 120.0, 125.0],
                bar_index=10, lifetime_bars=120)
        # Bar 1 hits TP1
        pm.on_confirmed_bar(open_p=100.0, high_p=106.0, low_p=98.0, close_p=105.0, bar_index=11)
        # Bar 2 hits TP2
        events = pm.on_confirmed_bar(open_p=105.0, high_p=111.0, low_p=104.0, close_p=110.0, bar_index=12)
        self.assertEqual(events[0]["event"], "TP2")
        # SL should now be moved to TP1 (105.0)
        self.assertEqual(pm.position.stop, 105.0)

    def test_timeout_closes_position(self):
        pm = SlsPositionManager()
        pm.open(entry=100.0, stop=95.0, targets=[105.0, 110.0, 115.0, 120.0, 125.0],
                bar_index=10, lifetime_bars=5)
        # Bar 11 to 15: no stop, no target
        for b in range(11, 16):
            events = pm.on_confirmed_bar(open_p=100.0, high_p=102.0, low_p=99.0, close_p=101.0, bar_index=b)
            self.assertEqual(len(events), 0)
        # Bar 16: bar_index - opened_bar = 6 > lifetime_bars (5) -> TIMEOUT
        events = pm.on_confirmed_bar(open_p=101.0, high_p=102.0, low_p=99.0, close_p=101.0, bar_index=16)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "CLOSE")
        self.assertEqual(events[0]["reason"], "TIMEOUT")
        self.assertFalse(pm.in_position)

    def test_store_register_and_advance_round_trip(self):
        state = {}
        signal = {
            "strategy": "SLS",
            "signal_action": "BUY",
            "coin": "ETH",
            "interval": "15m",
            "entry": 2000.0,
            "stop": 1950.0,
            "t1": 2050.0,
            "t2": 2100.0,
            "t3": 2150.0,
            "t4": 2200.0,
            "t5": 2250.0,
            "candle_open_time": 1000_000,
            "setup_type": "BRK",
            "score": 85,
        }
        registered = sls_position_store.register_signal(state, signal)
        self.assertTrue(registered)
        self.assertIn("ETH:15m", state)
        self.assertEqual(state["ETH:15m"]["status"], "open")
        self.assertEqual(sls_position_store.active_keys(state), {"ETH:15m"})

        # Try to register same coin/interval while open -> should return False
        dup = sls_position_store.register_signal(state, signal)
        self.assertFalse(dup)

        # Advance with candle touching TP1
        rows = [{"t": 1000_900, "o": 2000.0, "h": 2060.0, "l": 1990.0, "c": 2040.0}]
        events = sls_position_store.advance_position(state, "ETH", "15m", rows)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "TP1")
        self.assertTrue(state["ETH:15m"]["tp1"])
        # Stop loss should be trailed to entry
        self.assertEqual(state["ETH:15m"]["stop"], 2000.0)


if __name__ == "__main__":
    unittest.main()

