import unittest

from signals import STRATEGY_PROFILES, strategy_profile


class BuyRecallPrecisionTests(unittest.TestCase):
    def test_profiles_are_relaxed_for_recall(self):
        self.assertEqual(strategy_profile("5m")["min_score"], 55)
        self.assertEqual(strategy_profile("15m")["min_score"], 53)
        self.assertEqual(strategy_profile("1h")["min_score"], 52)
        self.assertEqual(strategy_profile("5m")["min_vol_x"], 1.15)
        self.assertEqual(strategy_profile("15m")["min_vol_x"], 1.05)
        self.assertEqual(strategy_profile("1h")["min_vol_x"], 1.00)

    def test_risk_reward_remains_bounded_and_target_atr_is_unchanged(self):
        self.assertEqual(STRATEGY_PROFILES["scalp_5m"]["min_rr"], 1.70)
        self.assertEqual(STRATEGY_PROFILES["scalp_15m"]["min_rr"], 1.60)
        self.assertEqual(STRATEGY_PROFILES["small_trade_1h"]["min_rr"], 1.50)
        self.assertEqual(STRATEGY_PROFILES["scalp_5m"]["target_atr"], 3.0)
        self.assertEqual(STRATEGY_PROFILES["scalp_15m"]["target_atr"], 4.0)
        self.assertEqual(STRATEGY_PROFILES["small_trade_1h"]["target_atr"], 5.5)

    def test_recall_floor_does_not_remove_the_hard_potential_envelope(self):
        self.assertGreaterEqual(STRATEGY_PROFILES["scalp_5m"]["min_rr"], 1.5)
        self.assertGreaterEqual(STRATEGY_PROFILES["scalp_15m"]["min_rr"], 1.5)
        self.assertGreaterEqual(STRATEGY_PROFILES["small_trade_1h"]["min_rr"], 1.5)


if __name__ == "__main__":
    unittest.main()
