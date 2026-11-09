import unittest

from exchange_adapter import NetworkInfo
from network_routes import canonical_network, resolve_route
from orderbook_depth import simulate_round_trip


class Phase8DepthNetworkTests(unittest.TestCase):
    def test_depth_walks_multiple_levels(self):
        result = simulate_round_trip(
            [[100.0, 1.0], [101.0, 2.0]],
            [[105.0, 0.5], [104.0, 2.0]],
            150.0,
        )
        self.assertTrue(result.complete)
        self.assertGreater(result.average_buy, 100.0)
        self.assertLess(result.average_sell, 105.0)
        self.assertEqual(result.levels_used_buy, 2)
        self.assertEqual(result.levels_used_sell, 2)

    def test_depth_rejects_incomplete_liquidity(self):
        result = simulate_round_trip([[100.0, 0.5]], [[105.0, 0.1]], 100.0)
        self.assertFalse(result.complete)

    def test_network_aliases_are_explicit(self):
        self.assertEqual(canonical_network("USDT-TRC20"), "TRX")
        self.assertEqual(canonical_network("TRON"), "TRX")
        source = [NetworkInfo("USDT-TRC20", True, True, 1.0, 5.0)]
        destination = [NetworkInfo("TRX", True, False, 0.0, 0.0)]
        route = resolve_route(source, destination)
        self.assertIsNone(route)

    def test_network_requires_withdraw_and_deposit_enabled(self):
        source = [NetworkInfo("ERC20", True, True, 1.0, 5.0)]
        destination = [NetworkInfo("ETH", True, True, 0.0, 0.0)]
        route = resolve_route(source, destination)
        self.assertIsNotNone(route)
        self.assertEqual(route.network, "ETH")


if __name__ == "__main__":
    unittest.main()
