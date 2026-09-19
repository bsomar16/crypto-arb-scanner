import os
import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from execution_guard import ExecutionRequest, validate_execution_order, validate_withdrawal_request


class ExecutionEngineTests(unittest.TestCase):
    def opp(self):
        return SimpleNamespace(
            symbol="SOLUSDT", buy_exchange="binance", sell_exchange="bybit",
            executable_notional_usdt=300.0, buy_ask=100.0, sell_bid=106.0,
            net_pct=5.0, transfer_required=False, network=None,
        )

    def test_confirmation_is_required_and_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine({"realtime_min_net_pct": 0.5}, d)
            intent = e.create_intent(self.opp())
            with self.assertRaises(PermissionError):
                e.confirm(intent, False)
            confirmed = e.confirm(intent, True)
            self.assertEqual(confirmed.status, "DRY_RUN_CONFIRMED")

    def test_notional_above_limit_is_rejected_instead_of_clamped(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine({"execution_max_notional_usdt": 100}, d)
            with self.assertRaises(ValueError):
                e.create_intent(SimpleNamespace(**{**self.opp().__dict__, "executable_notional_usdt": 101}))

    def test_withdrawal_requires_a_second_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("EXECUTION_ENABLED")
            os.environ["EXECUTION_ENABLED"] = "true"
            try:
                e = ExecutionEngine({"execution_max_notional_usdt": 300, "execution_live_enabled": True}, d)
                intent = e.create_intent(self.opp())
                e.confirm(intent, True, revalidator=lambda _: True)
                with self.assertRaises(PermissionError):
                    e.confirm_withdrawal(intent, False)
                self.assertFalse(intent.withdrawal_confirmed)
                e.confirm_withdrawal(intent, True)
                self.assertTrue(intent.withdrawal_confirmed)
            finally:
                if old is None:
                    os.environ.pop("EXECUTION_ENABLED", None)
                else:
                    os.environ["EXECUTION_ENABLED"] = old

    def test_execution_order_policy_blocks_market_orders_by_default(self):
        req = ExecutionRequest("SPOT", "BUY", "SOLUSDT", "BINANCE", 1, True, False, "MARKET")
        with self.assertRaises(PermissionError):
            validate_execution_order(req, cfg={"execution_allow_market_orders": False}, reference_price=100)

    def test_execution_order_policy_blocks_stale_or_bad_quality(self):
        req = ExecutionRequest("SPOT", "BUY", "SOLUSDT", "BINANCE", 1, True, False, "LIMIT", 100)
        with self.assertRaises(PermissionError):
            validate_execution_order(req, cfg={}, fresh=False)
        with self.assertRaises(PermissionError):
            validate_execution_order(req, cfg={}, market_quality={"risk_action": "BLOCK"})

    def test_withdrawal_policy_is_fail_closed(self):
        req = ExecutionRequest("SPOT", "SELL", "SOLUSDT", "BINANCE", 1, True, True)
        with self.assertRaises(PermissionError):
            validate_withdrawal_request(req, cfg={"execution_allow_withdrawals": False},
                                         network_enabled=True, destination_confirmed=True)

    def test_spot_gate_rejects_non_spot_requests_and_allows_spot(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine({"execution_live_enabled": True}, d)
            with self.assertRaises(ValueError):
                e.validate_order("binance", "BTCUSDT", 1, "BUY", confirmed=True, market_type="FUTURES")
            import os
            old = os.environ.get("EXECUTION_ENABLED")
            os.environ["EXECUTION_ENABLED"] = "true"
            try:
                e.validate_order("binance", "BTCUSDT", 0.01, "BUY", confirmed=True)
            finally:
                if old is None: os.environ.pop("EXECUTION_ENABLED", None)
                else: os.environ["EXECUTION_ENABLED"] = old


if __name__ == "__main__":
    unittest.main()
