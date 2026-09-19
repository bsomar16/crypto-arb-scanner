import os
import tempfile
import unittest
from unittest.mock import patch

from execution_engine import ExecutionEngine
from execution_guard import ExecutionRequest, validate_spot_request
from shadow_trading import run_once


class PaperLiveTransitionTests(unittest.TestCase):
    def test_live_requires_both_independent_gates(self):
        cfg = {"execution_live_enabled": True, "execution_max_notional_usdt": 300}
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "false"}, clear=False):
                self.assertFalse(ExecutionEngine(cfg, state_dir=state_dir).enabled)
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
                self.assertTrue(ExecutionEngine(cfg, state_dir=state_dir).enabled)

    def test_live_engine_order_validation_stays_spot_only(self):
        cfg = {"execution_live_enabled": True, "execution_max_notional_usdt": 300}
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
                engine = ExecutionEngine(cfg, state_dir=state_dir)
                with self.assertRaises(ValueError):
                    engine.validate_order(
                        "BINANCE",
                        "SOLUSDT",
                        1,
                        "BUY",
                        confirmed=True,
                        market_type="FUTURES",
                        price=100,
                    )

    def test_shadow_path_does_not_enable_live_execution(self):
        signal = {
            "coin": "SOL",
            "rating": "BUY",
            "entry": 100,
            "stop": 98,
            "t1": 102,
            "t2": 104,
            "t3": 106,
        }
        with tempfile.TemporaryDirectory() as state_dir:
            shadow_path = os.path.join(state_dir, "shadow.jsonl")
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "false"}, clear=False):
                engine = ExecutionEngine(
                    {"execution_live_enabled": False},
                    state_dir=state_dir,
                )
                result = run_once(
                    [signal],
                    lambda coin: 100,
                    {"shadow_notional_usdt": 300},
                    path=shadow_path,
                )
                self.assertEqual(result["opened"], 1)
                self.assertFalse(engine.enabled)
                self.assertTrue(os.path.exists(shadow_path))

    def test_spot_guard_rejects_forbidden_product_terms(self):
        req = ExecutionRequest(
            product="FUTURES",
            side="BUY",
            symbol="SOLUSDT",
            exchange="BINANCE",
            quantity=1,
            confirmed=True,
            order_type="LIMIT",
        )
        with self.assertRaises(ValueError):
            validate_spot_request(req)

    def test_live_defaults_to_fail_closed(self):
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
                engine = ExecutionEngine({}, state_dir=state_dir)
                self.assertFalse(engine.enabled)

    def test_paper_path_never_becomes_live_from_signal_confirmation(self):
        cfg = {
            "execution_live_enabled": False,
            "execution_max_notional_usdt": 300,
        }
        signal = {
            "coin": "SOL",
            "rating": "BUY",
            "entry": 100,
            "stop": 98,
            "t1": 102,
            "t2": 104,
            "t3": 106,
        }
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.dict(os.environ, {"EXECUTION_ENABLED": "true"}, clear=False):
                engine = ExecutionEngine(cfg, state_dir=state_dir)
                self.assertFalse(engine.enabled)
                result = run_once(
                    [signal],
                    lambda coin: 100,
                    {"shadow_notional_usdt": 300},
                    path=os.path.join(state_dir, "shadow.jsonl"),
                )
                self.assertEqual(result["opened"], 1)
                self.assertFalse(engine.enabled)
