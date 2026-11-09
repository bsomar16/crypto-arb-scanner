import unittest

from exchange_adapter import SpotMarket
from order_constraints import OrderConstraintError, normalize_spot_order


class OrderConstraintTests(unittest.TestCase):
    def setUp(self):
        self.market = SpotMarket(
            symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
            min_qty=0.001, min_notional=10.0, qty_step=0.0001, price_tick=0.1,
        )

    def test_quantity_and_price_are_floored(self):
        order = normalize_spot_order(self.market, 0.00129, 60123.19)
        self.assertEqual(order.quantity, 0.0012)
        self.assertEqual(order.price, 60123.1)
        self.assertAlmostEqual(order.notional, 72.14772, places=5)

    def test_below_min_quantity_fails_closed(self):
        with self.assertRaises(OrderConstraintError):
            normalize_spot_order(self.market, 0.00099, 60000)

    def test_below_min_notional_after_quantization_fails_closed(self):
        market = SpotMarket("ABCUSDT", "ABC", "USDT", 1, 100, 1, 0.01)
        with self.assertRaises(OrderConstraintError):
            normalize_spot_order(market, 1.9, 50)

    def test_invalid_price_fails_closed(self):
        with self.assertRaises(OrderConstraintError):
            normalize_spot_order(self.market, 0.002, 0)

    def test_no_price_still_quantizes_quantity(self):
        order = normalize_spot_order(self.market, 0.00129)
        self.assertEqual(order.quantity, 0.0012)
        self.assertIsNone(order.price)
        self.assertEqual(order.notional, 0.0)


if __name__ == "__main__":
    unittest.main()
