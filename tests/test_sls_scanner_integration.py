"""Unit tests for SLS strategy and scanner integration."""
import unittest
from unittest.mock import patch, MagicMock

import scanner
import sls_strategy
import sls_engine


class SlsScannerIntegrationTests(unittest.TestCase):
    def test_sls_is_enabled_by_default(self):
        self.assertTrue(scanner.DEFAULTS["sls_enabled"])
        self.assertEqual(scanner.DEFAULTS["sls_sensitivity"], "Balanced")

    def test_strategy_contract_is_spot_buy_only(self):
        import inspect
        source = inspect.getsource(sls_engine.evaluate_series)
        self.assertIn('"signal_action": "BUY"', source)
        self.assertNotIn('"signal_action": "SELL"', source)
        self.assertNotIn('"signal_action": "SHORT"', source)

    def test_sls_signal_formatting(self):
        signal = {
            "strategy": "SLS",
            "strategy_family": "SLS",
            "coin": "SOL",
            "interval": "15m",
            "setup_type": "EARLY_BRK",
            "entry": 150.0,
            "stop": 145.0,
            "t1": 155.0,
            "t2": 160.0,
            "t3": 170.0,
            "t4": 180.0,
            "t5": 190.0,
            "potential_pct": 13.3,
            "risk_pct": 3.33,
            "rr": 4.0,
            "score": 85.0,
            "sls_threshold": 70,
            "sls_regime": "R_BULL",
            "rsi": 62.0,
            "vol_x": 2.1,
            "reasons": ["Early volume surge", "Breakout above 5-bar high"],
        }
        msg = scanner._signal_message(signal)
        joined = "\n".join(msg)
        self.assertIn("SLS SPOT BUY SIGNAL", joined)
        self.assertIn("SLS v9", joined)
        self.assertIn("EARLY_BRK", joined)
        self.assertIn("TP1:", joined)
        self.assertIn("TP5:", joined)

    def test_rising_edge_suppresses_persistent_signal(self):
        # A static qualifying signal returned for both bars should be suppressed on the second bar
        rows = [{"t": i * 900_000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000, "q": 101000} for i in range(210)]
        mock_signal = {
            "strategy": "SLS", "signal_action": "BUY", "coin": "ETH", "interval": "15m",
            "entry": 101, "stop": 98, "t1": 103, "t2": 105, "t3": 108, "t4": 110, "t5": 112,
            "potential_pct": 6.9, "risk_pct": 2.97, "rr": 2.3, "score": 80, "sls_threshold": 70,
            "candle_open_time": rows[-1]["t"]
        }
        with patch.object(sls_strategy, "_candles", return_value=rows):
            with patch.object(sls_strategy, "evaluate_series", side_effect=[mock_signal, mock_signal]):
                res = sls_strategy.evaluate_sls_coin("ETH", "15m")
                self.assertIsNone(res)

    def test_rising_edge_emits_when_prior_bar_unqualified(self):
        rows = [{"t": i * 900_000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000, "q": 101000} for i in range(210)]
        mock_signal = {
            "strategy": "SLS", "signal_action": "BUY", "coin": "ETH", "interval": "15m",
            "entry": 101, "stop": 98, "t1": 103, "t2": 105, "t3": 108, "t4": 110, "t5": 112,
            "potential_pct": 6.9, "risk_pct": 2.97, "rr": 2.3, "score": 80, "sls_threshold": 70,
            "candle_open_time": rows[-1]["t"], "setup_type": "BRK"
        }
        with patch.object(sls_strategy, "_candles", return_value=rows):
            with patch.object(sls_strategy, "evaluate_series", side_effect=[mock_signal, None]):
                res = sls_strategy.evaluate_sls_coin("ETH", "15m")
                self.assertIsNotNone(res)
                self.assertEqual(res["strategy"], "SLS")


if __name__ == "__main__":
    unittest.main()

