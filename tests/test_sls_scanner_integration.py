import inspect
import unittest
from unittest.mock import patch

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


    def test_persistent_qualification_does_not_emit_second_buy(self):
        rows = [{"t": i * 300_000, "o": 100, "h": 102, "l": 99,
                 "c": 101, "v": 1000, "q": 101000} for i in range(206)]
        htf = [{"t": i * 3_600_000, "o": 100, "h": 102, "l": 99,
                "c": 101, "v": 1000, "q": 101000} for i in range(260)]
        signal = {"strategy": "SLS", "signal_source": "SLS", "signal_engine": "Spot Long Signal Engine", "signal_action": "BUY", "entry": 101,
                  "t3": 105, "score": 90, "sls_threshold": 86,
                  "sls_regime": "R_BULL", "sls_benchmark": "B_SUP",
                  "sls_components": {"c5": 4}, "sls_gates": {},
                  "candle_open_time": rows[-1]["t"]}
        def provider(symbol, interval, limit=240):
            return rows if interval == "5m" else htf
        with patch.object(sls_strategy, "evaluate_series",
                          side_effect=[signal, signal]) as evaluate:
            result = sls_strategy.evaluate_sls_coin(
                "ETH", "5m", candle_provider=provider)
        self.assertIsNone(result)
        self.assertEqual(evaluate.call_count, 2)

    def test_new_qualification_after_unqualified_previous_bar_emits_buy(self):
        rows = [{"t": i * 300_000, "o": 100, "h": 102, "l": 99,
                 "c": 101, "v": 1000, "q": 101000} for i in range(206)]
        htf = [{"t": i * 3_600_000, "o": 100, "h": 102, "l": 99,
                "c": 101, "v": 1000, "q": 101000} for i in range(260)]
        signal = {"strategy": "SLS", "signal_action": "BUY", "entry": 101,
                  "t3": 105, "score": 90, "sls_threshold": 86,
                  "sls_regime": "R_BULL", "sls_benchmark": "B_SUP",
                  "sls_components": {"c5": 4}, "sls_gates": {},
                  "candle_open_time": rows[-1]["t"]}
        def provider(symbol, interval, limit=240):
            return rows if interval == "5m" else htf
        with patch.object(sls_strategy, "evaluate_series",
                          side_effect=[signal, None]) as evaluate:
            result = sls_strategy.evaluate_sls_coin(
                "ETH", "5m", candle_provider=provider)
        self.assertIsNotNone(result)
        self.assertEqual(result["signal_source"], "SLS")
        self.assertEqual(result["signal_action"], "BUY")
        self.assertEqual(evaluate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
