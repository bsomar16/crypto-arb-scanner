import unittest
from unittest.mock import Mock

from live_tp_manager import LiveTPManager


class FakeEngine:
    enabled = True

    def validate_order(self, exchange, symbol, quantity, side, *, confirmed, market_type="SPOT"):
        self.validated = (exchange, symbol, quantity, side, confirmed, market_type)

    def revalidate_before_adapter(self, intent, callback):
        if not callback(intent):
            raise ValueError("revalidation failed")


class TestLiveTPManager(unittest.TestCase):
    def cfg(self):
        return {"tp1_allocation_pct": 30, "tp2_allocation_pct": 30, "tp3_allocation_pct": 40}

    def position(self):
        return {
            "position_id": "p1", "coin": "SOL", "exchange": "BINANCE", "status": "open",
            "entry": 100, "entry_fill_qty": 10, "remaining_qty": 10, "last_price": 105,
            "last_pct": 5, "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        }

    def test_allocation_is_30_30_40(self):
        mgr = LiveTPManager(FakeEngine())
        self.assertAlmostEqual(mgr.allocation(self.cfg(), "TP1"), .30)
        self.assertAlmostEqual(mgr.allocation(self.cfg(), "TP2"), .30)
        self.assertAlmostEqual(mgr.allocation(self.cfg(), "TP3"), .40)

    def test_requires_explicit_confirmation(self):
        mgr = LiveTPManager(FakeEngine())
        with self.assertRaises(PermissionError):
            mgr.execute_confirmed_exit(self.position(), "TP1", Mock(), cfg=self.cfg(),
                                       explicit_confirmation=False, revalidate=lambda _: True, price=105)

    def test_live_exit_uses_filled_quantity_and_updates_remaining(self):
        adapter = Mock()
        adapter.place_spot_order.return_value = {
            "orderId": "o1", "status": "FILLED", "executedQty": "3", "price": "105", "fee": "0.03"
        }
        position = self.position()
        mgr = LiveTPManager(FakeEngine())
        result = mgr.execute_confirmed_exit(position, "TP1", adapter, cfg=self.cfg(),
                                            explicit_confirmation=True, revalidate=lambda _: True, price=105)
        self.assertEqual(result.order_id, "o1")
        self.assertEqual(result.quantity, 3.0)
        self.assertEqual(position["remaining_qty"], 7.0)
        self.assertTrue(position["tp1_hit"])
        adapter.place_spot_order.assert_called_once()
        args = adapter.place_spot_order.call_args.args
        self.assertEqual(args[0], "SOLUSDT")
        self.assertEqual(args[1], "SELL")
        self.assertEqual(args[2], 3.0)

    def test_rejects_bad_allocation_total(self):
        mgr = LiveTPManager(FakeEngine())
        with self.assertRaises(ValueError):
            mgr.allocation({"tp1_allocation_pct": 20, "tp2_allocation_pct": 30, "tp3_allocation_pct": 40}, "TP1")


if __name__ == "__main__":
    unittest.main()
