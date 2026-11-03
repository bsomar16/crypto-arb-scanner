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
        self.assertEqual(cutoff, 4 * day + 1000)
        self.assertEqual({r["timestamp"] for r in test}, {4 * day + 1000})
        self.assertEqual({r["timestamp"] for r in train}, {1000, day + 1000})
        self.assertEqual({r["timestamp"] for r in purged}, {2 * day + 1000, 3 * day + 1000})

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

    def test_insufficient_future_is_censored_not_expired(self):
        signal = {"entry": 100, "stop": 95, "t1": 107.5, "t2": 112.5, "t3": 120}
        rows = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
                {"t": 1, "o": 100, "h": 102, "l": 99, "c": 101}]
        result = sls_backtest._evaluate_outcome(signal, rows, 0, 3, 0, 0)
        self.assertEqual(result["outcome"], "CENSORED")
        self.assertIsNone(result["net_return_pct"])


if __name__ == "__main__":
    unittest.main()
