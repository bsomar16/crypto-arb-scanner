import unittest

from signals import STRATEGY_PROFILES, strategy_profile


class BuyRecallPrecisionTests(unittest.TestCase):
    def test_profiles_are_modestly_relaxed(self):
        self.assertEqual(strategy_profile("5m")["min_score"], 59)
        self.assertEqual(strategy_profile("15m")["min_score"], 57)
        self.assertEqual(strategy_profile("1h")["min_score"], 55)
        self.assertEqual(strategy_profile("5m")["min_vol_x"], 1.25)
        self.assertEqual(strategy_profile("15m")["min_vol_x"], 1.15)
        self.assertEqual(strategy_profile("1h")["min_vol_x"], 1.05)

    def test_risk_reward_and_target_atr_are_unchanged(self):
        self.assertEqual(STRATEGY_PROFILES["scalp_5m"]["min_rr"], 1.80)
        self.assertEqual(STRATEGY_PROFILES["scalp_15m"]["min_rr"], 1.70)
        self.assertEqual(STRATEGY_PROFILES["small_trade_1h"]["min_rr"], 1.60)
        self.assertEqual(STRATEGY_PROFILES["scalp_5m"]["target_atr"], 3.0)
        self.assertEqual(STRATEGY_PROFILES["scalp_15m"]["target_atr"], 4.0)
        self.assertEqual(STRATEGY_PROFILES["small_trade_1h"]["target_atr"], 5.5)


if __name__ == "__main__":
    unittest.main()
