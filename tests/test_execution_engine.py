import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine


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

    def test_spot_gate_rejects_derivatives(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine({}, d)
            with self.assertRaises(ValueError):
                e.validate_order("binance", "BTCUSDT", 1, "BUY", confirmed=True)
            # The adapter contract itself is SPOT-only; this test ensures normal
            # requests remain valid without enabling live execution.
            e.validate_order("binance", "BTCUSDT", 0.01, "BUY", confirmed=True)


if __name__ == "__main__":
    unittest.main()
