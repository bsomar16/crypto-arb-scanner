#!/usr/bin/env python3
"""Pure-function unit tests: indicators, scores, chain summary, traps,
fees/net-spread, order-book slip size. Stdlib only (unittest)."""

import json
import os
import tempfile
import types
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
        # monotonic up -> rsi -> 100, monotonic down -> rsi -> 0
        up = list(range(1, 51))
        down = list(range(50, 0, -1))
        rsu = indicators.rsi(up, 14)
        rsd = indicators.rsi(down, 14)
        self.assertGreater(rsu, 70)
        self.assertLess(rsd, 30)
        self.assertGreaterEqual(rsu, 0)
        self.assertLessEqual(rsu, 100)

    def test_rsi_short_series(self):
        self.assertIsNone(indicators.rsi([1.0, 2.0], 14))


class TestSignals(unittest.TestCase):
    def test_score_daily_range(self):
        # minimal indicator dict; score must be finite 0..100
        d = {"rsi": 55.0, "close": 1.0, "ema20": 0.98, "ema50": 0.95,
             "macd": 0.001, "macd_signal": 0.0}
        for chg in (-5, 0, 5):
            s = signals.score_daily(d, 1.2, chg)
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(s, 100)
            self.assertEqual(s, float(s))

    def test_rating(self):
        self.assertEqual(signals.rating(55), "BUY")
        self.assertEqual(signals.rating(75), "STRONG BUY")
        self.assertEqual(signals.rating(30), "SELL")
        self.assertEqual(signals.rating(20), "STRONG SELL")
        self.assertEqual(signals.rating(45), "WATCH")


class _FakeChains(unittest.TestCase):
    def test_chain_summary_fully(self):
        # chain_summary lives in markets; import here (no network in tests)
        import markets
        chans = [("eth", True, True), ("bsc", True, True)]
        net, fully = markets.chain_summary(chans)
        self.assertTrue(fully)
        self.assertIsInstance(net, str)

    def test_chain_summary_partial(self):
        import markets
        chans = [("eth", True, False), ("bsc", False, True)]
        net, fully = markets.chain_summary(chans)
        self.assertFalse(fully)

    def test_calc_net(self):
        import markets
        # equal fees both sides: high=1.02*low nets ~ +1.99% vs gross +2%
        net = markets.calc_net(102.0, 100.0, "BINANCE", "BINANCE")
        self.assertAlmostEqual(net, (102 * 0.999) / (100 * 1.001) * 100 - 100, places=6)
        self.assertLess(net, 2.0)

    def test_taker_fee_env_override(self):
        import markets
        os.environ["FEE_GATE"] = "0.0005"
        try:
            self.assertEqual(markets.taker_fee("GATE"), 0.0005)
        finally:
            del os.environ["FEE_GATE"]


class TestSlippedSize(unittest.TestCase):
    def test_flat_book_no_slip(self):
        import markets
        levels = [(100.0, 100.0)] * 5  # 500 units at 100
        size = markets.slipped_size(levels, 100.0, 0.05)
        self.assertAlmostEqual(size, 500 * 100.0)

    def test_thin_book_capped(self):
        import markets
        # ask(100, qty 1) then ask(101, qty 1): moving avg crosses +1% fast
        levels = [(100.0, 1.0), (101.0, 1.0), (103.0, 100.0)]
        size = markets.slipped_size(levels, 100.0, 0.20)
        # avg to 101.x after 2 units is +1% < 20% -> continues until big jump
        self.assertGreaterEqual(size, 2.0 * 100.0)

    def test_sell_side(self):
        import markets
        levels = [(100.0, 2.0), (99.0, 2.0)]
        # -0.5% target=99.5: second level 99 -> avg 99.5 == target, stop before it
        size = markets.slipped_size(levels, 100.0, -0.005)
        self.assertAlmostEqual(size, 2.0 * 100.0)


class TestTraps(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "traps.json")
        self.now = 1_700_000_000.0

    def test_migration_flat_list(self):
        with open(self.path, "w") as f:
            json.dump(["COIN1", "COIN2"], f)
        traps_dict = traps.load_traps(self.path, expiry_days=7.0, now=self.now)
        self.assertEqual(set(traps_dict), {"COIN1", "COIN2"})
        # migrated file keeps timestamps
        for v in traps_dict.values():
            self.assertEqual(v, self.now)

    def test_prune_expired(self):
        traps_dict = traps.prune_expired_traps(
            {"OLD": self.now - 8 * 86400, "NEW": self.now - 1 * 86400},
            expiry_days=7.0, now=self.now)
        self.assertEqual(set(traps_dict), {"NEW"})

    def test_mark_flagged_keeps_first_ts(self):
        traps_dict = {"KEEP": self.now - 100}
        traps.mark_flagged(traps_dict, ["KEEP", "FRESH"], now=self.now)
        self.assertEqual(traps_dict["KEEP"], self.now - 100)
        self.assertEqual(traps_dict["FRESH"], self.now)

    def test_save_roundtrip(self):
        traps.save_traps(self.path, {"A": self.now})
        loaded = traps.load_traps(self.path, now=self.now)
        self.assertEqual(loaded, {"A": self.now})


class TestStoreAggregation(unittest.TestCase):
    def test_aggregate_empty(self):
        import store
        a = store.aggregate([], now=store._now())
        self.assertEqual(a["spreads"]["count"], 0)
        self.assertEqual(a["positions"]["opened"], 0)

    def test_aggregate_simple(self):
        import store
        rows = [
            {"kind": "spread", "coin": "ABC", "gross": 2.0, "net": 1.8,
             "ts": "2026-01-01T00:00:00+00:00"},
            {"kind": "daily", "coin": "ABC", "score": 80, "rating": "STRONG BUY",
             "ts": "2026-01-01T00:00:00+00:00"},
            {"kind": "position", "event": "open", "coin": "ABC", "entry": 1.0,
             "ts": "2026-01-01T00:00:00+00:00"},
            {"kind": "position", "event": "tp3", "coin": "ABC", "entry": 1.0,
             "ts": "2026-01-01T05:00:00+00:00"},
        ]
        a = store.aggregate(rows, now=store._parse_ts("2026-01-10T00:00:00+00:00"))
        self.assertEqual(a["spreads"]["count"], 1)
        self.assertEqual(a["ratings"]["STRONG BUY"], 1)
        self.assertEqual(a["positions"]["opened"], 1)
        self.assertEqual(a["positions"]["tp3"], 1)
        self.assertEqual(a["positions"]["win_rate"], 1.0)
        self.assertEqual(a["positions"]["tp1_avg_hours"], None)


class TestNamesDiverge(unittest.TestCase):
    def test_same(self):
        import markets
        self.assertFalse(markets.names_diverge("Bitcoin", "BITCOIN"))
        self.assertFalse(markets.names_diverge("Aave", "Aave (AAVE)"))

    def test_diff(self):
        import markets
        self.assertTrue(markets.names_diverge("Bitcoin", "Ethereum"))

    def test_unknown(self):
        import markets
        self.assertFalse(markets.names_diverge("", "Ethereum"))
        self.assertFalse(markets.names_diverge(None, None))


if __name__ == "__main__":
    unittest.main()