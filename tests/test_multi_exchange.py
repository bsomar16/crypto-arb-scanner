import unittest

import multi_exchange


class MultiExchangeTests(unittest.TestCase):
    def cfg(self):
        return {"multi_exchange_enabled": True, "multi_exchange_max_dispersion_pct": 1.5}

    def maps(self):
        return {
            "BINANCE": {"SOL": 100.0},
            "BYBIT": {"SOL": 100.2},
            "OKX": {"SOL": 99.9},
            "BITGET": {"SOL": 100.1},
            "MEXC": {"SOL": 100.0},
        }

    def test_consistent_spot_prices(self):
        result = multi_exchange.assess("SOL", cfg=self.cfg(), price_maps=self.maps())
        self.assertEqual(result["state"], "CONSISTENT")
        self.assertTrue(result["data_available"])
        self.assertEqual(result["exchange_count"], 5)
        self.assertEqual(result["modifier"], 1.0)

    def test_large_dispersion_is_flagged(self):
        maps = self.maps()
        maps["BYBIT"]["SOL"] = 103.0
        result = multi_exchange.assess("SOL", cfg=self.cfg(), price_maps=maps)
        self.assertEqual(result["state"], "DISPERSION")
        self.assertEqual(result["modifier"], -1.0)
        self.assertGreater(result["price_dispersion_pct"], 1.5)

    def test_fee_adjusted_gap_is_reported(self):
        result = multi_exchange.assess("SOL", cfg=self.cfg(), price_maps=self.maps())
        self.assertIn("fee_adjusted_gap_pct", result)
        self.assertGreaterEqual(result["fee_adjusted_gap_pct"], 0.0)

    def test_insufficient_venues_fail_open(self):
        maps = {"BINANCE": {"SOL": 100.0}}
        result = multi_exchange.assess("SOL", cfg=self.cfg(), price_maps=maps)
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertFalse(result["data_available"])
        self.assertEqual(result["modifier"], 0.0)


if __name__ == "__main__":
    unittest.main()
