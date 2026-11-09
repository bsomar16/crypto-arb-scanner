import os
import tempfile
import unittest
from unittest.mock import patch

from operational_controls import assert_controlled_live, live_gate_state, preflight_live_config


class OperationalControlTests(unittest.TestCase):
    def test_live_requires_both_gates_and_no_kill_switch(self):
        cfg = {
            "execution_live_enabled": True,
            "execution_max_notional_usdt": 300,
            "execution_max_active_intents": 2,
            "execution_max_daily_notional_usdt": 1000,
            "execution_allowed_exchanges": ["BINANCE"],
        }
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "false", "EXECUTION_KILL_SWITCH": "false"}):
            self.assertFalse(live_gate_state(cfg)["live_enabled"])
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "true"}):
            self.assertFalse(live_gate_state(cfg)["live_enabled"])
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "false"}):
            self.assertTrue(live_gate_state(cfg)["live_enabled"])

    def test_default_configuration_is_blocked(self):
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
            report = preflight_live_config({})
            self.assertEqual(report["status"], "BLOCKED")

    def test_enabled_policy_requires_operational_limits(self):
        cfg = {
            "execution_live_enabled": True,
            "execution_max_notional_usdt": 0,
            "execution_max_active_intents": 0,
            "execution_max_daily_notional_usdt": 0,
            "execution_allowed_exchanges": [],
        }
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "false"}):
            report = preflight_live_config(cfg)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertGreaterEqual(len(report["warnings"]), 4)

    def test_safe_policy_passes_preflight_without_exchange_access(self):
        cfg = {
            "execution_live_enabled": True,
            "execution_allow_market_orders": False,
            "execution_allow_withdrawals": False,
            "execution_max_notional_usdt": 300,
            "execution_max_active_intents": 2,
            "execution_max_daily_notional_usdt": 1000,
            "execution_allowed_exchanges": ["BINANCE", "BYBIT"],
        }
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "false"}):
            report = preflight_live_config(cfg)
            self.assertEqual(report["status"], "READY")
            assert_controlled_live(cfg)

    def test_kill_switch_blocks_even_when_both_gates_are_on(self):
        cfg = {
            "execution_live_enabled": True,
            "execution_max_notional_usdt": 300,
            "execution_max_active_intents": 2,
            "execution_max_daily_notional_usdt": 1000,
            "execution_allowed_exchanges": ["BINANCE"],
        }
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "true"}):
            with self.assertRaises(PermissionError):
                assert_controlled_live(cfg)
