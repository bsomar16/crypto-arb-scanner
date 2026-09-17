import unittest

from execution_monitor import reconcile_order


class FakeAdapter:
    def __init__(self, payload):
        self.payload = payload

    def get_order(self, symbol, order_id):
        return self.payload


class ExecutionMonitorTests(unittest.TestCase):
    def test_bybit_filled_order_and_fee_detail(self):
        adapter = FakeAdapter({
            "orderId": "bybit-1",
            "orderStatus": "Filled",
            "cumExecQty": "1.25",
            "avgPrice": "101.5",
            "cumExecValue": "126.875",
            "cumFeeDetail": {"SOL": "0.00125"},
        })
        snap = reconcile_order(adapter, "SOLUSDT", "bybit-1")
        self.assertEqual(snap.status, "FILLED")
        self.assertEqual(snap.executed_qty, 1.25)
        self.assertEqual(snap.avg_price, 101.5)
        self.assertEqual(snap.executed_quote_qty, 126.875)
        self.assertEqual(snap.fee_amount, 0.00125)
        self.assertEqual(snap.fee_currency, "SOL")

    def test_partial_and_terminal_statuses_are_normalized(self):
        for raw_status, expected in (("PartiallyFilled", "PARTIAL"), ("Cancelled", "CANCELLED"), ("Rejected", "REJECTED"), ("Expired", "EXPIRED")):
            snap = reconcile_order(FakeAdapter({"orderId": "x", "orderStatus": raw_status}), "SOLUSDT", "x")
            self.assertEqual(snap.status, expected)

    def test_fill_array_fee_fallback(self):
        snap = reconcile_order(FakeAdapter({
            "orderId": "binance-1",
            "status": "FILLED",
            "executedQty": "2",
            "avgPrice": "100",
            "fills": [
                {"commission": "0.001", "commissionAsset": "SOL"},
                {"commission": "0.002", "commissionAsset": "SOL"},
            ],
        }), "SOLUSDT", "binance-1")
        self.assertEqual(snap.fee_amount, 0.003)
        self.assertEqual(snap.fee_currency, "SOL")


if __name__ == "__main__":
    unittest.main()
