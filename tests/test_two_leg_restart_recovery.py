import json
import tempfile
import unittest
from pathlib import Path

from execution_engine import ExecutionEngine, ExecutionIntent
from two_leg_execution import LegFill, LegState
from two_leg_executor import TwoLegExecutor


class RestartRecoveryTests(unittest.TestCase):
    def intent(self):
        return ExecutionIntent("i1", "SOLUSDT", "BINANCE", "BYBIT", 100.0, 100.0, 101.0, 0.5, True, "SOL")

    def test_recovery_restores_submitted_buy_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as root:
            executor = TwoLegExecutor(ExecutionEngine({"execution_live_enabled": False}, state_dir=root), state_dir=root)
            c = executor.coordinator(self.intent(), 1.0)
            c.accept_buy(LegFill("buy-123", "NEW", 1.0, 0.0))
            recovered = executor.recover_coordinator(self.intent(), 1.0)
            self.assertEqual(recovered.intent.state, LegState.BUY_SUBMITTED)
            self.assertEqual(recovered.intent.buy_order_id, "buy-123")

    def test_recovery_rejects_submitted_buy_without_order_id(self):
        with tempfile.TemporaryDirectory() as root:
            executor = TwoLegExecutor(ExecutionEngine({"execution_live_enabled": False}, state_dir=root), state_dir=root)
            c = executor.coordinator(self.intent(), 1.0)
            c.intent.state = LegState.BUY_SUBMITTED
            c._save("RECOVERY_TEST")
            with self.assertRaises(RuntimeError):
                executor.recover_coordinator(self.intent(), 1.0)


if __name__ == "__main__":
    unittest.main()
