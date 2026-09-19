import os
import unittest
from unittest.mock import patch

from execution_engine import ExecutionEngine
from execution_guard import validate_spot_request
from shadow_trading import open_trade, run_once


class PaperLiveTransitionTests(unittest.TestCase):
    def test_live_requires_both_independent_gates(self):
        cfg = {"execution_live_enabled": True, "execution_max_notional_usdt": 300}
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "false"}, clear=False):
            self.assertFalse(ExecutionEngine(cfg, state_dir="state/test-transition").enabled)
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
            self.assertTrue(ExecutionEngine(cfg, state_dir="state/test-transition").enabled)

    def test_live_engine_order_validation_stays_spot_only(self):
        cfg = {"execution_live_enabled": True, "execution_max_notional_usdt": 300}
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
            engine = ExecutionEngine(cfg, state_dir="state/test-transition")
            with self.assertRaises(ValueError):
                engine.validate_order("BINANCE", "SOLUSDT", 1, "BUY",
                                      confirmed=True, market_type="FUTURES", price=100)

    def test_shadow_path_does_not_enable_live_execution(self):
        signal = {"coin": "SOL", "rating": "BUY", "entry": 100, "stop": 98,
                  "t1": 102, "t2": 104, "t3": 106}
        with patch.dict(os.environ, {"EXECUTION_ENABLED": "false"}, clear=False):
            engine = ExecutionEngine({"execution_live_enabled": False}, state_dir="state/test-transition")
            result = run_once([signal], lambda coin: 100, {"shadow_notional_usdt": 300},
                              path="state/test-transition/shadow.jsonl")
            self.assertEqual(result["opened"], 1)
            self.assertFalse(engine.enabled)

    def test_spot_guard_rejects_forbidden_product_terms(self):
        with self.assertRaises(ValueError):
            validate_spot_request({"product": "SPOT", "side": "BUY", "symbol": "SOLUSDT",
                                   "exchange": "BINANCE", "quantity": 1, "confirmed": True,
                                   "order_type": "LIMIT"})
