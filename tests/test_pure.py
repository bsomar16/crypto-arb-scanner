#!/usr/bin/env python3
"""Pure-function unit tests for the crypto-only scanner."""

import json
import os
import tempfile
import unittest

import indicators
import signals
import traps


class TestIndicators(unittest.TestCase):
    def test_ema_identity(self):
        xs = [10.0] * 50
        ema = indicators.ema(xs, 9)
        self.assertAlmostEqual(ema[-1], 10.0, places=6)
        self.assertEqual(len(ema), len(xs))

    def test_rsi_boundaries(self):
        up = list(range(1, 51)); down = list(range(50, 0, -1))
        rsu = indicators.rsi(up, 14); rsd = indicators.rsi(down, 14)
        self.assertGreater(rsu, 70); self.assertLess(rsd, 30)
        self.assertGreaterEqual(rsu, 0); self.assertLessEqual(rsu, 100)

    def test_rsi_short_series(self):
        self.assertIsNone(indicators.rsi([1.0, 2.0], 14))


class TestSignals(unittest.TestCase):
    def test_score_daily_range(self):
        d = {"rsi": 55.0, "close": 1.0, "e20": 0.98, "e50": 0.95, "macd": 0.001, "macd_sig": 0.0}
        for chg in (-5, 0, 5):
            s = signals.score_daily(d, 1.2, chg)
            self.assertGreaterEqual(s, 0); self.assertLessEqual(s, 100); self.assertEqual(s, float(s))

    def test_rating(self):
        self.assertEqual(signals.rating(85), "VERY STRONG")
        self.assertEqual(signals.rating(75), "STRONG BUY")
        self.assertEqual(signals.rating(65), "BUY")
        self.assertEqual(signals.rating(55), "WATCH")
        self.assertEqual(signals.rating(45), "AVOID")

    def test_valid_intervals(self):
        self.assertEqual(signals.VALID_INTERVALS, {"5m", "15m", "1h", "4h"})


class TestMarkets(unittest.TestCase):
    def test_chain_summary_fully(self):
        import markets
        net, fully = markets.chain_summary([("eth", True, True), ("bsc", True, True)])
        self.assertTrue(fully); self.assertIsInstance(net, str)

    def test_chain_summary_partial(self):
        import markets
        _, fully = markets.chain_summary([("eth", True, False), ("bsc", False, True)])
        self.assertFalse(fully)

    def test_calc_net(self):
        import markets
        net = markets.calc_net(102.0, 100.0, "BINANCE", "BINANCE")
        self.assertAlmostEqual(net, (102 * 0.999) / (100 * 1.001) * 100 - 100, places=6)
        self.assertLess(net, 2.0)

    def test_taker_fee_env_override(self):
        import markets
        os.environ["FEE_GATE"] = "0.0005"
        try: self.assertEqual(markets.taker_fee("GATE"), 0.0005)
        finally: del os.environ["FEE_GATE"]

    def test_no_stock_market_api(self):
        import markets
        self.assertFalse(hasattr(markets, "yahoo_chart"))
        self.assertFalse(hasattr(markets, "yahoo_quote"))


class TestSlippedSize(unittest.TestCase):
    def test_flat_book_no_slip(self):
        import markets
        self.assertAlmostEqual(markets.slipped_size([(100.0, 100.0)] * 5, 100.0, 0.05), 500 * 100.0)

    def test_thin_book_capped(self):
        import markets
        size = markets.slipped_size([(100.0, 1.0), (101.0, 1.0), (103.0, 100.0)], 100.0, 0.20)
        self.assertGreaterEqual(size, 2.0 * 100.0)

    def test_sell_side(self):
        import markets
        size = markets.slipped_size([(100.0, 2.0), (99.0, 2.0)], 100.0, -0.005)
        self.assertAlmostEqual(size, 2.0 * 100.0)


class TestBacktest(unittest.TestCase):
    def test_conservative_same_candle_stop_and_target(self):
        import backtest
        rows = [[0, 0, 106, 94, 100, 0], [1, 0, 106, 94, 100, 0]]
        signal = {"entry": 100.0, "stop": 95.0, "target": 105.0}
        result = backtest._evaluate(rows, 0, signal, 1)
        self.assertEqual(result["outcome"], "LOSS")

    def test_target_before_stop_is_win(self):
        import backtest
        rows = [[0, 0, 101, 99, 100, 0], [1, 0, 106, 99, 105, 0]]
        signal = {"entry": 100.0, "stop": 95.0, "target": 105.0}
        result = backtest._evaluate(rows, 0, signal, 1)
        self.assertEqual(result["outcome"], "WIN")

    def test_backtest_intervals(self):
        import backtest
        self.assertEqual(backtest.INTERVALS, ("5m", "15m", "1h"))


class TestTraps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.path = os.path.join(self.dir, "traps.json"); self.now = 1_700_000_000.0

    def test_migration_flat_list(self):
        with open(self.path, "w") as f: json.dump(["COIN1", "COIN2"], f)
        traps_dict = traps.load_traps(self.path, expiry_days=7.0, now=self.now)
        self.assertEqual(set(traps_dict), {"COIN1", "COIN2"})
        for v in traps_dict.values(): self.assertEqual(v, self.now)

    def test_prune_expired(self):
        traps_dict = traps.prune_expired_traps({"OLD": self.now - 8 * 86400, "NEW": self.now - 86400}, expiry_days=7.0, now=self.now)
        self.assertEqual(set(traps_dict), {"NEW"})

    def test_mark_flagged_keeps_first_ts(self):
        traps_dict = {"KEEP": self.now - 100}; traps.mark_flagged(traps_dict, ["KEEP", "FRESH"], now=self.now)
        self.assertEqual(traps_dict["KEEP"], self.now - 100); self.assertEqual(traps_dict["FRESH"], self.now)

    def test_save_roundtrip(self):
        traps.save_traps(self.path, {"A": self.now}); self.assertEqual(traps.load_traps(self.path, now=self.now), {"A": self.now})


class TestStoreAggregation(unittest.TestCase):
    def test_aggregate_empty(self):
        import store
        a = store.aggregate([], now=store._now())
        self.assertEqual(a["spreads"]["count"], 0); self.assertEqual(a["positions"]["opened"], 0)

    def test_aggregate_simple(self):
        import store
        rows = [
            {"kind":"spread","coin":"ABC","gross":2.0,"net":1.8,"ts":"2026-01-01T00:00:00+00:00"},
            {"kind":"daily","coin":"ABC","score":80,"rating":"STRONG BUY","ts":"2026-01-01T00:00:00+00:00"},
            {"kind":"position","event":"open","coin":"ABC","entry":1.0,"ts":"2026-01-01T00:00:00+00:00"},
            {"kind":"position","event":"tp3","coin":"ABC","entry":1.0,"ts":"2026-01-01T05:00:00+00:00"},
        ]
        a = store.aggregate(rows, now=store._parse_ts("2026-01-10T00:00:00+00:00"))
        self.assertEqual(a["spreads"]["count"], 1); self.assertEqual(a["ratings"]["STRONG BUY"], 1)
        self.assertEqual(a["positions"]["opened"], 1); self.assertEqual(a["positions"]["tp3"], 1)
        self.assertEqual(a["positions"]["win_rate"], 1.0); self.assertEqual(a["positions"]["tp1_avg_hours"], None)


class TestNamesDiverge(unittest.TestCase):
    def test_same(self):
        import markets
        self.assertFalse(markets.names_diverge("Bitcoin", "BITCOIN")); self.assertFalse(markets.names_diverge("Aave", "Aave (AAVE)"))

    def test_diff(self):
        import markets
        self.assertTrue(markets.names_diverge("Bitcoin", "Ethereum"))

    def test_unknown(self):
        import markets
        self.assertFalse(markets.names_diverge("", "Ethereum")); self.assertFalse(markets.names_diverge(None, None))


if __name__ == "__main__": unittest.main()
