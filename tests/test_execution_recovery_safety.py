import tempfile
import unittest

from execution_monitor import reconcile_order
from two_leg_executor import TwoLegExecutor
from execution_engine import ExecutionEngine


class UnknownOrderAdapter:
    def get_order(self, symbol, order_id):
        return {}


class RecoverySafetyTests(unittest.TestCase):
    def test_unknown_order_is_not_treated_as_retryable(self):
        snap = reconcile_order(UnknownOrderAdapter(), "SOLUSDT", "abc")
        self.assertEqual(snap.status, "UNKNOWN")
        with tempfile.TemporaryDirectory() as root:
            executor = TwoLegExecutor(ExecutionEngine({"execution_live_enabled": False}, state_dir=root), state_dir=root)
            self.assertEqual(executor.transfers.transfers, {})


if __name__ == "__main__":
    unittest.main()
