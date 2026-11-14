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

    def test_intervals_support_30m_and_45m(self):
        self.assertIn("30m", sls_strategy.INTERVALS)
        self.assertIn("45m", sls_strategy.INTERVALS)
        self.assertIn("30m", sls_strategy.HTF_MAP)
        self.assertIn("45m", sls_strategy.HTF_MAP)
        self.assertEqual(sls_strategy._interval_close_ms(0, "30m"), 1800000)
        self.assertEqual(sls_strategy._interval_close_ms(0, "45m"), 2700000)
        self.assertIn("30m", scanner.DEFAULTS["buy_intervals"])
        self.assertIn("45m", scanner.DEFAULTS["buy_intervals"])

    def test_45m_resampling_aggregation(self):
        # Create 6 15m bars covering two 45m buckets:
        # Bucket 1: 0, 900_000, 1_800_000 (0 to 45m)
        # Bucket 2: 2_700_000, 3_600_000, 4_500_000 (45m to 90m)
        bars_15m = [
            {"t": 0, "o": 10.0, "h": 12.0, "l": 9.5, "c": 11.0, "v": 100.0, "q": 1100.0},
            {"t": 900_000, "o": 11.0, "h": 13.0, "l": 10.5, "c": 12.5, "v": 150.0, "q": 1800.0},
            {"t": 1_800_000, "o": 12.5, "h": 14.0, "l": 11.8, "c": 13.0, "v": 200.0, "q": 2600.0},
            {"t": 2_700_000, "o": 13.0, "h": 15.0, "l": 12.8, "c": 14.0, "v": 120.0, "q": 1600.0},
            {"t": 3_600_000, "o": 14.0, "h": 16.5, "l": 13.5, "c": 15.5, "v": 180.0, "q": 2700.0},
            {"t": 4_500_000, "o": 15.5, "h": 17.0, "l": 14.8, "c": 16.0, "v": 220.0, "q": 3500.0},
        ]
        # Evaluate as of time after bucket 2 closes (e.g. 6_000_000)
        res = sls_strategy.resample_15m_to_45m(bars_15m, now_ms=6_000_000)
        self.assertEqual(len(res), 2)
        
        # Check first 45m bar
        b1 = res[0]
        self.assertEqual(b1["t"], 0)
        self.assertEqual(b1["o"], 10.0)
        self.assertEqual(b1["h"], 14.0)
        self.assertEqual(b1["l"], 9.5)
        self.assertEqual(b1["c"], 13.0)
        self.assertEqual(b1["v"], 450.0)
        self.assertEqual(b1["q"], 5500.0)

        # Check second 45m bar
        b2 = res[1]
        self.assertEqual(b2["t"], 2_700_000)
        self.assertEqual(b2["o"], 13.0)
        self.assertEqual(b2["h"], 17.0)
        self.assertEqual(b2["l"], 12.8)
        self.assertEqual(b2["c"], 16.0)
        self.assertEqual(b2["v"], 520.0)
        self.assertEqual(b2["q"], 7800.0)

    def test_evaluate_sls_coin_on_45m(self):
        # Verify evaluate_sls_coin executes cleanly on 45m
        bars_45m = [{"t": i * 2_700_000, "o": 100, "h": 103, "l": 98, "c": 102, "v": 3000, "q": 306000} for i in range(215)]
        mock_signal = {
            "strategy": "SLS", "signal_action": "BUY", "coin": "SOL", "interval": "45m",
            "entry": 102, "stop": 97, "t1": 105, "t2": 108, "t3": 112, "t4": 115, "t5": 118,
            "potential_pct": 10.0, "risk_pct": 3.0, "rr": 3.33, "score": 85, "sls_threshold": 70,
            "candle_open_time": bars_45m[-1]["t"], "setup_type": "EARLY_BRK"
        }
        with patch.object(sls_strategy, "_candles", return_value=bars_45m):
            with patch.object(sls_strategy, "evaluate_series", side_effect=[mock_signal, None]):
                res = sls_strategy.evaluate_sls_coin("SOL", "45m")
                self.assertIsNotNone(res)
                self.assertEqual(res["interval"], "45m")
                self.assertEqual(res["setup_type"], "EARLY_BRK")
                self.assertEqual(res["trade_horizon"], "Medium")

    def test_setup_upgrade_emits_when_prior_bar_was_early(self):
        # When prior bar was an EARLY_BRK and current bar confirms as BRK, rising edge must NOT suppress it
        rows = [{"t": i * 900_000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000, "q": 101000} for i in range(210)]
        curr_signal = {
            "strategy": "SLS", "signal_action": "BUY", "coin": "ETH", "interval": "15m",
            "entry": 101, "stop": 98, "t1": 103, "t2": 105, "t3": 108, "t4": 110, "t5": 112,
            "potential_pct": 6.9, "risk_pct": 2.97, "rr": 2.3, "score": 82, "sls_threshold": 70,
            "candle_open_time": rows[-1]["t"], "setup_type": "BRK"
        }
        prev_signal = {
            "strategy": "SLS", "signal_action": "BUY", "coin": "ETH", "interval": "15m",
            "entry": 100, "stop": 97, "t1": 102, "t2": 104, "t3": 107, "t4": 109, "t5": 111,
            "potential_pct": 7.0, "risk_pct": 3.0, "rr": 2.3, "score": 75, "sls_threshold": 70,
            "candle_open_time": rows[-2]["t"], "setup_type": "EARLY_BRK"
        }
        with patch.object(sls_strategy, "_candles", return_value=rows):
            with patch.object(sls_strategy, "evaluate_series", side_effect=[curr_signal, prev_signal]):
                res = sls_strategy.evaluate_sls_coin("ETH", "15m")
                self.assertIsNotNone(res)
                self.assertEqual(res["setup_type"], "BRK")
                self.assertEqual(res["setup_upgrade_from"], "EARLY_BRK")
                self.assertTrue(any("Confirmed upgrade: EARLY_BRK" in r for r in res["reasons"]))

    def test_mtf_confluence_annotation_and_ranking_boost(self):
        # Two hits on same coin with different intervals
        hit_15m = {
            "coin": "SOL", "interval": "15m", "strategy": "SLS", "setup_type": "BRK",
            "entry": 150.0, "stop": 145.0, "t1": 155.0, "t2": 160.0, "t3": 170.0,
            "potential_pct": 13.3, "risk_pct": 3.33, "rr": 4.0, "score": 80.0,
            "sls_threshold": 70, "candle_open_time": 1000
        }
        hit_45m = {
            "coin": "SOL", "interval": "45m", "strategy": "SLS", "setup_type": "EARLY_BRK",
            "entry": 151.0, "stop": 144.0, "t1": 156.0, "t2": 162.0, "t3": 172.0,
            "potential_pct": 14.0, "risk_pct": 4.6, "rr": 3.0, "score": 85.0,
            "sls_threshold": 70, "candle_open_time": 1000
        }
        fresh, updates = scanner._dedupe_buy_signals([hit_15m, hit_45m], {}, 1000)
        # Should consolidate into 1 best signal (highest score 45m)
        self.assertEqual(len(fresh), 1)
        best = fresh[0]
        self.assertEqual(best["coin"], "SOL")
        self.assertEqual(best["interval"], "45m")
        self.assertEqual(best["mtf_confluence_intervals"], ["15m", "45m"])
        self.assertEqual(best["mtf_confluence_count"], 2)

        # Signal message should display MTF Confluence
        msg = scanner._signal_message(best)
        joined = "\n".join(msg)
        self.assertIn("MTF Confluence", joined)
        self.assertIn("15m + 45m", joined)

        # Ranking boost
        ranked = scanner._rank_trade_candidates([best], {})
        self.assertEqual(ranked[0]["mtf_confluence_boost"], 5.0)

    def test_15m_and_45m_cache_sharing(self):
        sls_strategy._CACHE.clear()
        mock_raw = [[i * 900_000, "10", "12", "9", "11", "100", 0, "1000"] for i in range(750)]
        with patch.object(sls_strategy, "http_json", return_value=mock_raw) as mock_http:
            # 1. Fetch 15m (requests 240, but fetches 750 for 45m reuse)
            bars_15m = sls_strategy._candles("AVAX", "15m", limit=240)
            self.assertIsNotNone(bars_15m)
            self.assertEqual(len(bars_15m), 240)
            self.assertEqual(mock_http.call_count, 1)

            # 2. Fetch 45m (should use cached 750 15m bars with ZERO additional HTTP calls)
            bars_45m = sls_strategy._candles("AVAX", "45m", limit=240)
            self.assertIsNotNone(bars_45m)
            self.assertEqual(mock_http.call_count, 1)  # STILL 1, no new HTTP calls!


if __name__ == "__main__":
    unittest.main()

