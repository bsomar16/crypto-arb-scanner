import datetime
import unittest

import sls_backtest


class SlsBacktestTests(unittest.TestCase):

    def test_global_split_keeps_same_timestamp_in_same_partition(self):
        day = 86400000
        records = [
            {"coin": "ETH", "interval": "5m", "timestamp": 1000, "outcome": "TP3", "hits": [1, 1, 1], "net_return_pct": 1},
            {"coin": "BTC", "interval": "1h", "timestamp": 1000, "outcome": "STOP", "hits": [0, 0, 0], "net_return_pct": -1},
            {"coin": "ETH", "interval": "5m", "timestamp": day + 1000, "outcome": "TP3", "hits": [1, 1, 1], "net_return_pct": 1},
            {"coin": "BTC", "interval": "1d", "timestamp": 2 * day + 1000, "outcome": "STOP", "hits": [0, 0, 0], "net_return_pct": -1},
            {"coin": "ETH", "interval": "5m", "timestamp": 3 * day + 1000, "outcome": "TP3", "hits": [1, 1, 1], "net_return_pct": 1},
            {"coin": "ETH", "interval": "5m", "timestamp": 4 * day + 1000, "outcome": "TP3", "hits": [1, 1, 1], "net_return_pct": 1},
        ]
        cutoff, train, test, purged = sls_backtest._global_temporal_split(
            records, 0.6, {"5m": 1, "1h": 1, "1d": 2}
        )
        self.assertEqual(cutoff, 3 * day + 1000)
        self.assertEqual({r["timestamp"] for r in test}, {3 * day + 1000, 4 * day + 1000})
        self.assertEqual({r["timestamp"] for r in train}, {1000, day + 1000})
        self.assertEqual({r["timestamp"] for r in purged}, {2 * day + 1000})

    def test_global_split_purges_training_labels_crossing_cutoff(self):
        day = 86400000
        records = [
            {"interval": "1d", "timestamp": 0},
            {"interval": "1d", "timestamp": day},
            {"interval": "1d", "timestamp": 2 * day},
            {"interval": "1d", "timestamp": 3 * day},
        ]
        cutoff, train, test, purged = sls_backtest._global_temporal_split(
            records, 0.5, {"1d": 2}
        )
        self.assertEqual(cutoff, 2 * day)
        self.assertEqual([r["timestamp"] for r in train], [0])
        self.assertEqual([r["timestamp"] for r in purged], [day])
        self.assertEqual([r["timestamp"] for r in test], [2 * day, 3 * day])


    def test_single_timestamp_is_oos_and_not_misreported_as_purged(self):
        records = [
            {"interval": "5m", "timestamp": 123456},
            {"interval": "1h", "timestamp": 123456},
        ]
        cutoff, train, test, purged = sls_backtest._global_temporal_split(records, 0.7)
        self.assertEqual(cutoff, 123456)
        self.assertEqual(train, [])
        self.assertEqual(test, records)
        self.assertEqual(purged, [])


    def test_dataset_quality_reports_internal_fixed_interval_gaps(self):
        rows = [
            {"t": i * 300000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "q": 1}
            for i in (0, 1, 3, 4)
        ]
        data = sls_backtest._normalise_dataset({"candles": {"ETH": {"5m": rows}}})
        quality = sls_backtest._dataset_quality(data)
        item = quality["by_symbol_interval"]["ETH"]["5m"]
        self.assertEqual(item["candles"], 4)
        self.assertEqual(item["internal_missing_candles"], 1)
        self.assertEqual(item["coverage_pct"], 80.0)
        self.assertEqual(item["gaps"][0]["missing_candles"], 1)

    def test_dataset_quality_handles_calendar_month_gaps(self):
        jan = sls_backtest._candle_close_ms(
            int(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000),
            "1M",
        )
        mar = int(datetime.datetime(2024, 3, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        data = sls_backtest._normalise_dataset({"candles": {"BTC": {"1M": [
            {"t": int(datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000),
             "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "q": 1},
            {"t": mar, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "q": 1},
        ]}}})
        quality = sls_backtest._dataset_quality(data)
        item = quality["by_symbol_interval"]["BTC"]["1M"]
        self.assertEqual(item["internal_missing_candles"], 1)
        self.assertEqual(item["gaps"][0]["before_open_ms"], mar)
        self.assertEqual(quality["btc_benchmark"]["5m"]["candles"], 0)

    def test_provider_exposes_only_fully_closed_candles(self):
        dataset = {"candles": {"ETH": {"5m": [
            {"t": i * 300000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "q": 1}
            for i in range(205)
        ]}}}
        data = sls_backtest._normalise_dataset(dataset)
        fetch = sls_backtest._provider(data, 200 * 300000)
        rows = fetch("ETH", "5m")
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows[-1]["t"], 199 * 300000)

    def test_stop_has_priority_when_stop_and_target_touch_same_candle(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 1, "o": 100, "h": 121, "l": 94, "c": 110},
        ]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 1, 0, 0)
        self.assertEqual(result["outcome"], "STOP")
        self.assertEqual(result["net_return_pct"], -5.0)
        self.assertEqual(result["hits"], [False, False, False])

    def test_tp3_outcome_records_all_staged_targets(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 1, "o": 100, "h": 121, "l": 99, "c": 120},
        ]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 1, 0, 0)
        self.assertEqual(result["outcome"], "TP3")
        self.assertEqual(result["hits"], [True, True, True])
        self.assertEqual(result["net_return_pct"], 20.0)


    def test_execution_uses_next_candle_open_not_signal_close(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 1, "o": 110, "h": 121, "l": 109, "c": 120},
        ]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 1, 0, 0)
        self.assertEqual(result["execution_entry"], 110)
        self.assertEqual(result["outcome"], "TP3")
        self.assertAlmostEqual(result["net_return_pct"], (120 / 110 - 1) * 100, places=4)

    def test_gap_through_stop_is_filled_at_worse_next_open(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 1, "o": 90, "h": 100, "l": 89, "c": 95},
        ]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 1, 0, 0)
        self.assertEqual(result["outcome"], "INVALIDATED")
        self.assertEqual(result["execution_entry"], 90)
        self.assertIsNone(result["net_return_pct"])


    def test_gap_above_first_target_invalidates_stale_entry(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [
            {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 1, "o": 108, "h": 121, "l": 107, "c": 120},
        ]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 1, 0, 0)
        self.assertEqual(result["outcome"], "INVALIDATED")
        self.assertIsNone(result["net_return_pct"])

    def test_stats_report_expectancy_and_profit_factor(self):
        records = [
            {"outcome": "TP3", "hits": [True, True, True], "net_return_pct": 10.0},
            {"outcome": "STOP", "hits": [False, False, False], "net_return_pct": -5.0},
            {"outcome": "CENSORED", "hits": [False, False, False], "net_return_pct": None},
        ]
        stats = sls_backtest._stats(records)
        self.assertEqual(stats["expectancy_per_resolved_signal_pct"], 2.5)
        self.assertEqual(stats["profit_factor"], 2.0)
        self.assertEqual(stats["resolved_samples"], 2)

    def test_insufficient_future_is_censored_not_expired(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
                {"t": 1, "o": 100, "h": 102, "l": 99, "c": 101}]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 3, 0, 0)
        self.assertEqual(result["outcome"], "CENSORED")
        self.assertIsNone(result["net_return_pct"])


if __name__ == "__main__":
    unittest.main()
