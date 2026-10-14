import unittest

import replay_validate as rv


def _bar(high, low, open_ms=0):
    return [open_ms, low, high, low, high, 0]


class NeededMinutesTests(unittest.TestCase):
    def test_scales_with_interval_and_horizon(self):
        # 1h horizon of 48 bars -> 48 * 60 minutes
        self.assertEqual(rv.needed_minutes("1h", {}), 48 * 60)

    def test_config_override_is_honoured(self):
        self.assertEqual(rv.needed_minutes("1h", {"outcome_horizon_bars": {"1h": 2}}), 120)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self._fetch, self._max = rv.fetch_minute_window, rv.MAX_MINUTES

    def tearDown(self):
        rv.fetch_minute_window, rv.MAX_MINUTES = self._fetch, self._max

    def _run(self, bars):
        rv.fetch_minute_window = lambda *a, **k: bars
        return rv.resolve("BTC", 0, 100.0, 5.0, 5.0, "1h", {}, 3000)

    def test_target_first_when_high_leads(self):
        verdict, bars = self._run([_bar(103, 99), _bar(106, 101)])
        self.assertEqual(verdict, "TARGET_FIRST")
        self.assertEqual(bars, 2)

    def test_stop_first_when_low_leads(self):
        # bar 1 breaches the stop only; bar 2 would have hit the target later
        verdict, bars = self._run([_bar(101, 94), _bar(106, 99)])
        self.assertEqual(verdict, "STOP_FIRST")
        self.assertEqual(bars, 1)

    def test_same_bar_is_ambiguous_and_kept_conservative(self):
        # one 1m bar contains both levels -> 1m still cannot order it
        verdict, _ = self._run([_bar(106, 94)])
        self.assertEqual(verdict, "SAME_BAR")

    def test_neither_when_levels_untouched(self):
        verdict, _ = self._run([_bar(101, 99), _bar(102, 98)])
        self.assertEqual(verdict, "NEITHER")

    def test_no_data_when_fetch_returns_nothing(self):
        rv.fetch_minute_window = lambda *a, **k: []
        self.assertEqual(rv.resolve("BTC", 0, 100.0, 5.0, 5.0, "1h", {}, 3000)[0], "NO_DATA")

    def test_long_horizon_is_skipped_not_fetched(self):
        rv.MAX_MINUTES = 10
        called = []
        rv.fetch_minute_window = lambda *a, **k: called.append(1) or []
        verdict, _ = rv.resolve("BTC", 0, 100.0, 5.0, 5.0, "1h", {}, 3000)
        self.assertEqual(verdict, "SKIPPED")
        self.assertEqual(called, [])        # never hit the network


class FetchPaginationTests(unittest.TestCase):
    def setUp(self):
        self._fetch = rv.bf.fetch_window

    def tearDown(self):
        rv.bf.fetch_window = self._fetch

    def test_paginates_until_target_bars(self):
        calls = []

        def fake(symbol, interval, start, limit):
            calls.append((interval, start, limit))
            base = int(start)
            return [[base + i * rv.MINUTE, 1, 2, 0.5, 1.5, 0] for i in range(limit)]

        rv.bf.fetch_window = fake
        rows = rv.fetch_minute_window("BTC", 1000, minutes=2500, max_bars=2500)
        self.assertEqual(len(rows), 2500)
        self.assertTrue(all(c[0] == "1m" for c in calls))
        self.assertGreaterEqual(len(calls), 3)
