import tempfile
import unittest
from pathlib import Path

from execution_monitor import normalize_order_status, reconcile_order
from transfer_tracker import Transfer, TransferTracker


class FakeAdapter:
    def get_order(self, symbol, order_id):
        return {"symbol": symbol, "orderId": order_id, "status": "PARTIALLY_FILLED", "executedQty": "0.4", "avgPrice": "100"}


class ExecutionMonitorTransferTests(unittest.TestCase):
    def test_status_normalization(self):
        self.assertEqual(normalize_order_status({"orderStatus": "Filled"}), "FILLED")
        self.assertEqual(normalize_order_status({"status": "CANCELED"}), "CANCELLED")

    def test_reconcile_order(self):
        snap = reconcile_order(FakeAdapter(), "SOLUSDT", "1")
        self.assertEqual(snap.status, "PARTIAL")
        self.assertAlmostEqual(snap.executed_qty, 0.4)
        self.assertAlmostEqual(snap.avg_price, 100)

    def test_transfer_recovery_and_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = TransferTracker(d)
            t = tracker.create(Transfer("tx1", "SOL", 1.0, "binance", "bybit", "SOL"))
            tracker.transition("tx1", "SUBMITTED", updated_ms=1)
            tracker.transition("tx1", "CONFIRMING", txid="abc", updated_ms=2)
            tracker.transition("tx1", "COMPLETED", updated_ms=3)
            recovered = TransferTracker(d)
            self.assertEqual(recovered.transfers["tx1"].status, "COMPLETED")
            self.assertEqual(recovered.transfers["tx1"].txid, "abc")

    def test_invalid_transfer_transition_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = TransferTracker(d)
            tracker.create(Transfer("tx1", "SOL", 1.0, "binance", "bybit", "SOL"))
            with self.assertRaises(ValueError):
                tracker.transition("tx1", "COMPLETED")


if __name__ == "__main__":
    unittest.main()
