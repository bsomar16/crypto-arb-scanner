import unittest

from execution_guard import ExecutionRequest, validate_execution_order


class ControlledExecutionTests(unittest.TestCase):
    def req(self):
        return ExecutionRequest(
            product="SPOT", side="BUY", symbol="SOLUSDT",
            exchange="BINANCE", quantity=1.0, confirmed=True,
            order_type="LIMIT", price=100.0,
        )

    def test_missing_live_policy_fails_closed(self):
        with self.assertRaises(PermissionError):
            validate_execution_order(self.req(), cfg={})

    def test_config_disabled_fails_closed(self):
        with self.assertRaises(PermissionError):
            validate_execution_order(self.req(), cfg={"execution_live_enabled": False})

    def test_market_order_remains_disabled(self):
        req = self.req()
        req = ExecutionRequest(**{**req.__dict__, "order_type":"MARKET"})
        with self.assertRaises(PermissionError):
            validate_execution_order(req, cfg={"execution_live_enabled": True})

    def test_spot_limit_can_pass_policy(self):
        result = validate_execution_order(
            self.req(),
            cfg={"execution_live_enabled": True, "execution_max_notional_usdt": 300},
            reference_price=100,
        )
        self.assertEqual(result["status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
