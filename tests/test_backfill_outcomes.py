import json
import os
import tempfile
import unittest

import backfill_outcomes as bf


class MaturityTests(unittest.TestCase):
    def test_immature_when_horizon_not_elapsed(self):
        now = 1_800_000_000_000
        sig = {"candle_open_time": now - 3_600_000}          # one 1h bar ago
        self.assertFalse(bf.is_mature(sig, "1h", {}, now))

    def test_mature_when_horizon_elapsed(self):
        now = 1_800_000_000_000
        bars = bf.horizon_bars("1h", {}) + 3
        sig = {"candle_open_time": now - bars * 3_600_000}
        self.assertTrue(bf.is_mature(sig, "1h", {}, now))

    def test_missing_open_time_is_not_mature(self):
        self.assertFalse(bf.is_mature({}, "1h", {}, 1_800_000_000_000))

    def test_unknown_interval_is_not_mature(self):
        self.assertFalse(bf.is_mature({"candle_open_time": 1}, "3m", {}, 1_800_000_000_000))


class HorizonTests(unittest.TestCase):
    def test_config_override_wins(self):
        self.assertEqual(bf.horizon_bars("1h", {"outcome_horizon_bars": {"1h": 7}}), 7)

    def test_defaults_used_when_absent(self):
        self.assertEqual(bf.horizon_bars("5m", {}), bf.so.DEFAULT_HORIZON_BARS["5m"])


class LoadSignalsTests(unittest.TestCase):
    def test_skips_outcome_rows_and_blank_lines(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "h.jsonl")
        with open(p, "w") as fh:
            fh.write("\n")
            fh.write(json.dumps({"kind": "outcome", "id": "X"}) + "\n")
            fh.write(json.dumps({"kind": "signal", "id": "A", "interval": "1h"}) + "\n")
            fh.write("{not json}\n")
        self.assertEqual(list(bf.load_signals(p)), ["A"])


class GradeOneTests(unittest.TestCase):
    def setUp(self):
        self._fetch, self._eval = bf.fetch_window, bf.so._evaluate

    def tearDown(self):
        bf.fetch_window, bf.so._evaluate = self._fetch, self._eval

    def test_none_when_no_klines(self):
        bf.fetch_window = lambda *a, **k: []
        self.assertIsNone(bf.grade_one({"interval": "1h", "candle_open_time": 1}, {}))

    def test_none_when_grader_rejects(self):
        bf.fetch_window = lambda *a, **k: [[1, 1, 1, 1, 1, 0]]
        bf.so._evaluate = lambda *a, **k: None
        self.assertIsNone(bf.grade_one({"interval": "1h", "candle_open_time": 1}, {}))

    def test_result_is_enriched_for_the_report_join(self):
        bf.fetch_window = lambda *a, **k: [[1, 1, 1, 1, 1, 0]]
        bf.so._evaluate = lambda *a, **k: {"outcome": "WIN", "mfe_pct": 6.0, "mae_pct": -1.0}
        out = bf.grade_one({"interval": "1h", "candle_open_time": 1, "coin": "BTC",
                            "setup_type": "MOMENTUM"}, {})
        self.assertEqual(out["outcome"], "WIN")
        for k in ("coin", "interval", "setup_type", "evaluated_at", "backfilled"):
            self.assertIn(k, out)
        self.assertTrue(out["backfilled"])


class DryRunTests(unittest.TestCase):
    def setUp(self):
        # guard the shared module objects so this class cannot leak into other tests
        self._fetch, self._eval, self._now = bf.fetch_window, bf.so._evaluate, bf.now_ms

    def tearDown(self):
        bf.fetch_window, bf.so._evaluate, bf.now_ms = self._fetch, self._eval, self._now

    def test_dry_run_writes_merged_file_and_leaves_live_state_alone(self):
        d = tempfile.mkdtemp()
        hist = os.path.join(d, "h.jsonl")
        live = os.path.join(d, "live.json")
        out = os.path.join(d, "out.json")
        now_ms = 1_800_000_000_000
        open_time = now_ms - (bf.horizon_bars("1h", {}) + 5) * 3_600_000
        with open(hist, "w") as fh:
            fh.write(json.dumps({"kind": "signal", "id": "A|1h|MOMENTUM|1", "interval": "1h",
                                 "coin": "BTC", "candle_open_time": open_time, "ts": "2026-01-01T00:00:00+00:00"}) + "\n")
        with open(live, "w") as fh:
            json.dump({"EXISTING|1h|X|1": {"outcome": "WIN", "mfe_pct": 1.0, "mae_pct": -1.0}}, fh)
        before = open(live).read()

        bf.now_ms = lambda: now_ms
        bf.fetch_window = lambda *a, **k: [[open_time, 1, 1, 1, 1, 0]]
        bf.so._evaluate = lambda *a, **k: {"outcome": "WIN", "mfe_pct": 5.0, "mae_pct": -1.0}
        rc = bf.main(["--history", hist, "--outcomes", live, "--out", out, "--limit", "5", "--workers", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(open(live).read(), before)          # live state untouched
        merged = json.load(open(out))
        self.assertIn("EXISTING|1h|X|1", merged)             # existing preserved
        self.assertIn("A|1h|MOMENTUM|1", merged)             # new appended
