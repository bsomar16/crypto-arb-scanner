import unittest
from unittest.mock import patch

import exposure


class ExposureProfileTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "max_pairwise_correlation": 0.88,
            "correlation_interval": "1h",
            "correlation_lookback_bars": 72,
            "correlation_risk_hard_block": False,
        }

    @patch("exposure._returns")
    def test_high_correlation_is_review_by_default(self, returns):
        returns.side_effect = lambda coin, interval, limit: [0.01, -0.005, 0.012, 0.008, -0.002] * 5
        profile = exposure.assess_candidate(
            "SOL",
            [{"coin": "ETH", "status": "open"}],
            self.cfg,
        )
        self.assertTrue(profile["correlated"])
        self.assertEqual(profile["risk_action"], "REVIEW")
        self.assertTrue(profile["data_available"])
        self.assertEqual(profile["conflicts"][0]["coin"], "ETH")

    @patch("exposure._returns")
    def test_hard_block_is_opt_in(self, returns):
        returns.side_effect = lambda coin, interval, limit: [0.01, -0.005, 0.012, 0.008, -0.002] * 5
        cfg = dict(self.cfg, correlation_risk_hard_block=True)
        profile = exposure.assess_candidate(
            "SOL",
            [{"coin": "ETH", "status": "open"}],
            cfg,
        )
        self.assertEqual(profile["risk_action"], "BLOCK")

    @patch("exposure._returns")
    def test_low_correlation_is_allowed(self, returns):
        returns.side_effect = lambda coin, interval, limit: (
            [0.01, -0.005, 0.012, 0.008, -0.002] * 5
            if coin == "SOL"
            else [-0.01, 0.005, -0.012, -0.008, 0.002] * 5
        )
        profile = exposure.assess_candidate(
            "SOL",
            [{"coin": "ETH", "status": "open"}],
            self.cfg,
        )
        self.assertFalse(profile["correlated"])
        self.assertEqual(profile["risk_action"], "ALLOW")

    @patch("exposure._returns", return_value=None)
    def test_missing_data_fails_open(self, returns):
        profile = exposure.assess_candidate(
            "SOL",
            [{"coin": "ETH", "status": "open"}],
            self.cfg,
        )
        self.assertFalse(profile["correlated"])
        self.assertEqual(profile["risk_action"], "ALLOW")
        self.assertFalse(profile["data_available"])

    def test_no_open_positions_is_neutral(self):
        profile = exposure.assess_candidate("SOL", [], self.cfg)
        self.assertFalse(profile["correlated"])
        self.assertEqual(profile["risk_action"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
