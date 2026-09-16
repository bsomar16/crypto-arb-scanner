import unittest

from realtime_market import BBO
from realtime_opportunity import OpportunityEngine
from execution_guard import ExecutionRequest, validate_spot_request


class Phase5Tests(unittest.TestCase):
    def test_net_opportunity_after_fees(self):
        cfg = {
            "realtime_max_age_ms": 1500,
            "realtime_notional_usdt": 50,
            "realtime_min_net_pct": 0.5,
            "realtime_slippage_reserve_pct": 0.2,
            "spot_taker_fee_pct": {"binance": 0.1, "bybit": 0.1},
        }
        e = OpportunityEngine(cfg)
        buy = BBO("binance", "BTCUSDT", 100, 1, 100, 1, 1000)
        sell = BBO("bybit", "BTCUSDT", 101, 1, 101, 1, 1000)
        opp = e.evaluate(buy, sell, now_ms=1100)
        self.assertIsNotNone(opp)
        self.assertAlmostEqual(opp.gross_pct, 1.0)
        self.assertAlmostEqual(opp.net_pct, 0.6)

    def test_stale_data_rejected(self):
        e = OpportunityEngine({"spot_taker_fee_pct": {"a": 0.1, "b": 0.1}})
        buy = BBO("a", "BTCUSDT", 100, 1, 100, 1, 0)
        sell = BBO("b", "BTCUSDT", 101, 1, 101, 1, 0)
        self.assertIsNone(e.evaluate(buy, sell, now_ms=5000))

    def test_missing_fee_rejected(self):
        e = OpportunityEngine({"realtime_min_net_pct": 0})
        buy = BBO("a", "BTCUSDT", 100, 1, 100, 1, 1000)
        sell = BBO("b", "BTCUSDT", 101, 1, 101, 1, 1000)
        self.assertIsNone(e.evaluate(buy, sell, now_ms=1000))

    def test_spot_guard_requires_confirmation(self):
        with self.assertRaises(PermissionError):
            validate_spot_request(ExecutionRequest("SPOT", "BUY", "BTCUSDT", "binance", 0.01))

    def test_spot_guard_rejects_derivatives(self):
        req = ExecutionRequest("FUTURES", "BUY", "BTCUSDT", "binance", 0.01, True)
        with self.assertRaises(ValueError):
            validate_spot_request(req)


if __name__ == "__main__":
    unittest.main()
