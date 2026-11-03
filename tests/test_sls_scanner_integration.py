import inspect
import unittest

import scanner
import sls_strategy
import sls_engine


class SlsScannerIntegrationTests(unittest.TestCase):
    def test_default_schedule_covers_all_sls_intervals(self):
        self.assertEqual(scanner.DEFAULTS["buy_intervals"], list(sls_strategy.INTERVALS))

    def test_sls_is_enabled_by_default(self):
        self.assertTrue(scanner.DEFAULTS["sls_enabled"])

    def test_strategy_contract_is_spot_buy_only(self):
        source = inspect.getsource(sls_engine.evaluate_series)
        self.assertIn('"signal_action":"BUY"', source)
        self.assertNotIn('"signal_action":"SELL"', source)
        self.assertNotIn('"signal_action":"SHORT"', source)


if __name__ == "__main__":
    unittest.main()
