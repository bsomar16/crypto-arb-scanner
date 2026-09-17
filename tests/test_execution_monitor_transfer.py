import tempfile
import unittest

from execution_monitor import normalize_order_status, reconcile_order
from transfer_tracker import Transfer, TransferTracker


class FakeAdapter:
    def __init__(self, payload):
        self.payload = payload

    def get_order(self, symbol, order_id):
        return dict(self.payload)


class ExecutionMonitorTransferTests(unittest.TestCase):
    def test_status_normalization(self):
        self.assertEqual(normalize_order_status({"orderStatus": "Filled"}), "FILLED")
        self.assertEqual(normalize_order_status({"status": "CANCELED"}), "CANCELLED")
        self.assertEqual(normalize_order_status({"state": "live"}), "OPEN")
        self.assertEqual(normalize_order_status({"state": "partially_filled"}), "PARTIAL")

    def test_reconcile_binance_order(self):
        snap = reconcile_order(FakeAdapter({
            "orderId": 101,
            "status": "FILLED",
            "executedQty": "1.25",
            "cummulativeQuoteQty": "125.50",
            "fills": [{"commission": "0.00125", "commissionAsset": "SOL"}],
        }), "SOLUSDT", "101")
        self.assertEqual(snap.status, "FILLED")
        self.assertAlmostEqual(snap.executed_qty, 1.25)
        self.assertAlmostEqual(snap.executed_quote_qty, 125.50)
        self.assertAlmostEqual(snap.fee_amount, 0.00125)
        self.assertEqual(snap.fee_currency, "SOL")

    def test_reconcile_bybit_order(self):
        snap = reconcile_order(FakeAdapter({
            "orderId": "b1",
            "orderStatus": "PartiallyFilled",
            "cumExecQty": "0.4",
            "avgPrice": "100.25",
            "cumExecValue": "40.10",
            "cumExecFee": "0.0401",
            "feeCurrency": "USDT",
        }), "SOLUSDT", "b1")
        self.assertEqual(snap.status, "PARTIAL")
        self.assertAlmostEqual(snap.executed_qty, 0.4)
        self.assertAlmostEqual(snap.avg_price, 100.25)
        self.assertAlmostEqual(snap.executed_quote_qty, 40.10)
        self.assertAlmostEqual(snap.fee_amount, 0.0401)
        self.assertEqual(snap.fee_currency, "USDT")

    def test_reconcile_okx_order(self):
        snap = reconcile_order(FakeAdapter({
            "ordId": "o1",
            "state": "filled",
            "accFillSz": "2",
            "avgPx": "50.5",
            "accFillValue": "101",
            "fillFee": "0.02",
            "fillFeeCcy": "USDT",
        }), "SOL-USDT", "o1")
        self.assertEqual(snap.status, "FILLED")
        self.assertAlmostEqual(snap.executed_qty, 2)
        self.assertAlmostEqual(snap.avg_price, 50.5)
        self.assertAlmostEqual(snap.executed_quote_qty, 101)
        self.assertAlmostEqual(snap.fee_amount, 0.02)
        self.assertEqual(snap.fee_currency, "USDT")

    def test_reconcile_bitget_order(self):
        snap = reconcile_order(FakeAdapter({
            "orderId": "g1",
            "status": "full-fill",
            "baseVolume": "3",
            "priceAvg": "20",
            "quoteVolume": "60",
            "fee": "0.03",
            "feeCcy": "USDT",
        }), "SOLUSDT", "g1")
        self.assertEqual(snap.status, "UNKNOWN")
        self.assertAlmostEqual(snap.executed_qty, 3)
        self.assertAlmostEqual(snap.avg_price, 20)
        self.assertAlmostEqual(snap.executed_quote_qty, 60)
        self.assertAlmostEqual(snap.fee_amount, 0.03)
        self.assertEqual(snap.fee_currency, "USDT")

    def test_reconcile_mexc_order(self):
        snap = reconcile_order(FakeAdapter({
            "orderId": "m1",
            "status": "FILLED",
            "dealQuantity": "4",
            "avgPrice": "10",
            "dealAmount": "40",
            "fee": "0.04",
            "feeCurrency": "USDT",
        }), "SOLUSDT", "m1")
        self.assertEqual(snap.status, "FILLED")
        self.assertAlmostEqual(snap.executed_qty, 4)
        self.assertAlmostEqual(snap.avg_price, 10)
        self.assertAlmostEqual(snap.executed_quote_qty, 40)
        self.assertAlmostEqual(snap.fee_amount, 0.04)
        self.assertEqual(snap.fee_currency, "USDT")

    def test_transfer_recovery_and_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = TransferTracker(d)
            tracker.create(Transfer("tx1", "SOL", 1.0, "binance", "bybit", "SOL"))
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
