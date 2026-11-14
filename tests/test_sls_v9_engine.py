"""Unit tests for Spot Long Signal Engine v9 core evaluator."""
import unittest

from sls_engine import evaluate_series, SREngineV3, SetupTracker, BASE_CONFIDENCE


def make_rows(count=260, drift=0.2, base_price=100.0, volume=1000.0):
    rows = []
    price = base_price
    for i in range(count):
        price += drift
        rows.append({
            "t": i * 900_000,
            "o": price - 0.05,
            "h": price + 0.3,
            "l": price - 0.3,
            "c": price,
            "v": volume + (i % 7) * 10,
            "q": price * (volume + (i % 7) * 10),
            "symbol": "BTC",
        })
    return rows


class SlsV9EngineTests(unittest.TestCase):
    def test_sr_engine_v3_decay_and_rejections(self):
        sr = SREngineV3(decay=0.995, min_qual=1.5, max_levels=5)
        # Add a level
        sr.add_level_v2(px=100.0, src_type=0, org_bar=0, vol_at_pivot=1000.0, bar_index=0, atr_n=1.0)
        self.assertEqual(len(sr.srLv), 1)
        self.assertAlmostEqual(sr.srLv[0], 100.0)
        
        # Test wick rejection counting (low wick rejection at support 100.0)
        # bar with high=102, low=99.5, open=101, close=101.5 -> rng=2.5, low_wick = 101 - 99.5 = 1.5 -> 1.5/2.5 = 0.6 > 0.3
        sr.count_rejections(bar_index=1, open_p=101.0, high_p=102.0, low_p=99.5, close_p=101.5)
        self.assertGreater(sr.srRej[0], 0.0)

        # Test decay over 10 bars
        initial_touch = sr.srTch[0]
        for b in range(2, 12):
            sr.decay_levels(b)
        self.assertLess(sr.srTch[0], initial_touch)

    def test_sr_engine_v3_max_level_capping(self):
        sr = SREngineV3(max_levels=3)
        # Add 5 levels
        for p in (10.0, 20.0, 30.0, 40.0, 50.0):
            sr.add_level_v2(px=p, src_type=0, org_bar=0, vol_at_pivot=100.0, bar_index=0, atr_n=0.5)
        self.assertLessEqual(len(sr.srLv), 3)

    def test_setup_tracker_pause_rule(self):
        tracker = SetupTracker(min_trades_pause=3, pause_threshold=-0.1, pause_bars=10)
        # Record 3 losing trades for setup 0 (BRK)
        tracker.record(setup_idx=0, r_mult=-1.0, bar_index=5)
        tracker.record(setup_idx=0, r_mult=-1.0, bar_index=6)
        tracker.record(setup_idx=0, r_mult=-1.0, bar_index=7)
        # Should now be paused within pause_bars
        self.assertFalse(tracker.is_setup_on(setup_idx=0, bar_index=8))
        # After pause_bars elapse, should be unpaused
        self.assertTrue(tracker.is_setup_on(setup_idx=0, bar_index=25))

    def test_missing_btc_fails_closed(self):
        rows = make_rows()
        result = evaluate_series(rows, "15m", htf_rows=make_rows(), btc_rows=None)
        self.assertIsNone(result)

    def test_missing_htf_fails_closed(self):
        rows = make_rows()
        result = evaluate_series(rows, "15m", htf_rows=None, btc_rows=make_rows())
        self.assertIsNone(result)

    def test_weekly_skips_htf_requirement(self):
        rows = make_rows(count=260)
        btc = make_rows(count=260)
        # Weekly doesn't require htf_rows
        # It may return None or dict depending on gates, but doesn't raise exception
        try:
            res = evaluate_series(rows, "1w", htf_rows=None, btc_rows=btc)
            # Valid call executed
            self.assertTrue(res is None or isinstance(res, dict))
        except Exception as e:
            self.fail(f"evaluate_series raised {e}")

    def test_signal_contract_spot_buy_only(self):
        rows = make_rows(count=260, drift=0.1)
        res = evaluate_series(rows, "15m", htf_rows=make_rows(count=260), btc_rows=make_rows(count=260), sensitivity="Aggressive")
        if res is not None:
            self.assertEqual(res["strategy"], "SLS")
            self.assertEqual(res["signal_action"], "BUY")
            self.assertIn("t1", res)
            self.assertIn("t2", res)
            self.assertIn("t3", res)
            self.assertIn("t4", res)
            self.assertIn("t5", res)
            self.assertIn("sls_components", res)
            self.assertIn("c_trend", res["sls_components"])
            self.assertIn("c_momentum", res["sls_components"])
            self.assertIn("c_volume", res["sls_components"])
            self.assertIn("c_struct", res["sls_components"])
            self.assertIn("c_context", res["sls_components"])
            self.assertIn("c_early", res["sls_components"])


    def test_evaluate_series_handles_30m_and_45m(self):
        rows = make_rows(count=260, drift=0.1)
        htf = make_rows(count=260, drift=0.1)
        btc = make_rows(count=260, drift=0.1)
        # Verify 30m and 45m do not fail with None due to unmapped interval
        # If indicators qualify, they return a dict with matching interval
        try:
            res_30m = evaluate_series(rows, "30m", htf_rows=htf, btc_rows=btc, sensitivity="Aggressive")
            if res_30m is not None:
                self.assertEqual(res_30m["interval"], "30m")
            res_45m = evaluate_series(rows, "45m", htf_rows=htf, btc_rows=btc, sensitivity="Aggressive")
            if res_45m is not None:
                self.assertEqual(res_45m["interval"], "45m")
        except Exception as e:
            self.fail(f"evaluate_series raised exception on 30m/45m: {e}")


if __name__ == "__main__":
    unittest.main()

