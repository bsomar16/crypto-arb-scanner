import unittest
from unittest.mock import patch

import market_quality


def book(ask=100.0, bid=99.9, levels=None):
    asks = levels or [(ask, 100.0), (ask * 1.001, 100.0), (ask * 1.003, 100.0)]
    bids = [(bid, 100.0), (bid * 0.999, 100.0)]
    return {"asks": asks, "bids": bids}


class MarketQualityTests(unittest.TestCase):
    def base_cfg(self):
        return {
            "market_quality_enabled": True,
            "market_quality_notional_usdt": 1000.0,
            "market_quality_max_spread_pct": 0.35,
            "market_quality_max_slippage_pct": 0.25,
            "market_quality_min_depth_usdt": 10000.0,
            "market_quality_hard_block": False,
        }

    def test_liquid_book_has_low_slippage(self):
        result = market_quality.assess("SOL", cfg=self.base_cfg(), orderbook=book())
        self.assertEqual(result["state"], "LIQUID")
        self.assertTrue(result["data_available"])
        self.assertLess(result["estimated_slippage_pct"], 0.25)
        self.assertGreaterEqual(result["filled_pct"], 100.0)
        self.assertEqual(result["risk_action"], "ALLOW")

    def test_wide_spread_is_flagged(self):
        cfg = self.base_cfg()
        result = market_quality.assess(
            "THIN",
            cfg=cfg,
            orderbook=book(ask=101.0, bid=99.0),
        )
        self.assertEqual(result["state"], "WIDE_SPREAD")
        self.assertEqual(result["modifier"], -3.0)
        self.assertEqual(result["risk_action"], "ALLOW")

    def test_insufficient_depth_is_thin(self):
        cfg = self.base_cfg()
        cfg["market_quality_min_depth_usdt"] = 50000.0
        result = market_quality.assess(
            "THIN",
            cfg=cfg,
            orderbook=book(levels=[(100.0, 10.0), (100.1, 10.0)]),
        )
        self.assertEqual(result["state"], "THIN")
        self.assertEqual(result["risk_action"], "ALLOW")

    def test_hard_block_is_opt_in(self):
        cfg = self.base_cfg()
        cfg["market_quality_hard_block"] = True
        result = market_quality.assess(
            "THIN",
            cfg=cfg,
            orderbook=book(ask=101.0, bid=99.0),
        )
        self.assertEqual(result["risk_action"], "BLOCK")

    @patch("market_quality.fetch_orderbook", return_value=None)
    def test_missing_data_fails_open(self, fetch):
        result = market_quality.assess("UNKNOWN", cfg=self.base_cfg())
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["risk_action"], "ALLOW")
        self.assertFalse(result["data_available"])

    def test_disabled_is_neutral(self):
        cfg = self.base_cfg()
        cfg["market_quality_enabled"] = False
        result = market_quality.assess("SOL", cfg=cfg, orderbook=book())
        self.assertEqual(result["state"], "DISABLED")
        self.assertEqual(result["risk_action"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
