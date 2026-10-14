import unittest

import expectancy_report as er


class SimulateTests(unittest.TestCase):
    def test_stop_wins_when_both_touched(self):
        # conservative ordering: a stop breach anywhere counts as the loss
        self.assertEqual(er.simulate(mfe=8.0, mae=-6.0, target_pct=5.0, stop_pct=5.0), -5.0)

    def test_target_when_only_target_touched(self):
        self.assertEqual(er.simulate(mfe=6.0, mae=-1.0, target_pct=5.0, stop_pct=5.0), 5.0)

    def test_flat_when_neither_touched(self):
        self.assertEqual(er.simulate(mfe=1.0, mae=-1.0, target_pct=5.0, stop_pct=5.0), 0.0)

    def test_cost_is_round_trip(self):
        self.assertAlmostEqual(er.cost_pct(10.0, 5.0), 0.30, places=6)


class StatsTests(unittest.TestCase):
    def test_expectancy_is_net_of_costs(self):
        s = er.stats([5.0, -5.0, 0.0], 0.30)
        self.assertEqual(s["n"], 3)
        self.assertAlmostEqual(s["expectancy_pct"], -0.30, places=3)
        self.assertAlmostEqual(s["win_pct"], 33.3, places=1)

    def test_profit_factor_at_breakeven(self):
        s = er.stats([5.0, -5.0], 0.0)
        self.assertAlmostEqual(s["profit_factor"], 1.0, places=2)

    def test_empty_is_not_a_crash(self):
        self.assertEqual(er.stats([], 0.30)["n"], 0)


class WalkForwardTests(unittest.TestCase):
    def test_folds_are_time_ordered_and_partition_the_sample(self):
        rows = [{"mfe": 5.0, "mae": -1.0, "ts": f"2026-01-{d:02d}T00:00:00+00:00"} for d in range(1, 9)]
        wf = er.walk_forward(rows, 4, 5.0, 5.0, 0.0)
        self.assertEqual(len(wf), 4)
        self.assertEqual(wf[0]["from"], "2026-01-01")
        self.assertEqual(wf[-1]["to"], "2026-01-08")
        self.assertEqual([f["n"] for f in wf], [2, 2, 2, 2])


class LoadRowsTests(unittest.TestCase):
    def test_join_skips_signals_without_graded_excursion(self):
        import json, os, tempfile
        d = tempfile.mkdtemp()
        hist = os.path.join(d, "h.jsonl")
        outs = os.path.join(d, "o.json")
        with open(hist, "w") as fh:
            fh.write(json.dumps({"kind": "signal", "id": "A|1h|MOMENTUM|1", "ts": "2026-01-01T00:00:00+00:00",
                                 "interval": "1h", "setup_type": "MOMENTUM"}) + "\n")
            fh.write(json.dumps({"kind": "signal", "id": "B|1h|MOMENTUM|1", "ts": "2026-01-01T00:00:00+00:00"}) + "\n")
            fh.write(json.dumps({"kind": "outcome", "id": "A|1h|MOMENTUM|1"}) + "\n")
        with open(outs, "w") as fh:
            json.dump({"A|1h|MOMENTUM|1": {"outcome": "WIN", "mfe_pct": 6.0, "mae_pct": -2.0},
                       "B|1h|MOMENTUM|1": {"outcome": "LOSS", "mfe_pct": None, "mae_pct": None}}, fh)
        rows = er.load_rows(hist, outs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "A|1h|MOMENTUM|1")
        self.assertEqual(rows[0]["mfe"], 6.0)


if __name__ == "__main__":
    unittest.main()


def _mk(mfe, mae, ts, **kw):
    row = {"id": f"{ts}-{mfe}-{mae}", "mfe": mfe, "mae": mae, "ts": ts}
    row.update(kw)
    return row


class FoldPositiveTests(unittest.TestCase):
    def test_all_winners_are_positive_in_every_fold(self):
        rows = [_mk(9.0, -0.5, f"2026-01-{d:02d}T00:00:00+00:00") for d in range(1, 9)]
        pos, tot = er.fold_positive(rows, 5.0, 5.0, 0.0, 4)
        self.assertEqual((pos, tot), (4, 4))


class CalibrateTests(unittest.TestCase):
    def test_ignores_groups_below_min_n(self):
        rows = [_mk(9.0, -0.5, f"2026-01-{d:02d}T00:00:00+00:00", setup_type="TINY") for d in range(1, 4)]
        cal = er.calibrate(rows, ["setup_type"], [1.0, 5.0], [1.0, 5.0], 0.0, min_n=10, folds=2, need_pos=1)
        self.assertEqual(cal, [])

    def test_picks_the_highest_expectancy_cell(self):
        # losers only: no cell should be positive, and it still returns the best one
        rows = [_mk(0.5, -9.0, f"2026-01-{d:02d}T00:00:00+00:00", setup_type="LOSERS") for d in range(1, 9)]
        cal = er.calibrate(rows, ["setup_type"], [1.0, 5.0], [1.0, 5.0], 0.0, min_n=4, folds=2, need_pos=1)
        self.assertEqual(len(cal), 1)
        self.assertFalse(cal[0]["stable"])

    def test_stable_requires_positive_folds(self):
        rows = [_mk(9.0, -0.5, f"2026-01-{d:02d}T00:00:00+00:00", setup_type="GOOD") for d in range(1, 9)]
        cal = er.calibrate(rows, ["setup_type"], [5.0], [5.0], 0.0, min_n=4, folds=4, need_pos=3)
        self.assertTrue(cal[0]["stable"])
        self.assertEqual(cal[0]["fold_pos"], 4)


class FilteredUniverseTests(unittest.TestCase):
    def test_weights_expectancy_by_group_size(self):
        rows = ([_mk(9.0, -0.5, f"2026-01-{d:02d}T00:00:00+00:00", setup_type="A") for d in range(1, 5)]
                + [_mk(0.5, -9.0, f"2026-02-{d:02d}T00:00:00+00:00", setup_type="B") for d in range(1, 3)])
        cal = [
            {"key": "setup_type", "group": "A", "n": 4, "expectancy_pct": 5.0, "stable": True},
            {"key": "setup_type", "group": "B", "n": 2, "expectancy_pct": -5.0, "stable": False},
        ]
        fu = er.filtered_universe(rows, cal, 0.0)
        self.assertEqual(fu["groups"], 1)
        self.assertEqual(fu["signals"], 4)
        self.assertAlmostEqual(fu["expectancy_pct"], 5.0, places=3)

    def test_no_stable_groups(self):
        fu = er.filtered_universe([], [], 0.0)
        self.assertEqual(fu["groups"], 0)
        self.assertIsNone(fu["expectancy_pct"])


class RealizedTests(unittest.TestCase):
    def test_pnl_comes_from_recorded_exit_not_excursions(self):
        rows = [{"entry": 100.0, "exit_price": 97.0, "mfe": 9.0, "mae": -9.0}]
        self.assertAlmostEqual(er.realized_pnls(rows)[0], -3.0, places=6)

    def test_rows_without_exit_are_skipped(self):
        rows = [{"entry": 100.0, "exit_price": None}, {"entry": None, "exit_price": 90.0}]
        self.assertEqual(er.realized_pnls(rows), [])

    def test_realized_stats_is_net_of_cost(self):
        rows = [{"entry": 100.0, "exit_price": 110.0, "graded_outcome": "WIN"},
                {"entry": 100.0, "exit_price": 90.0, "graded_outcome": "LOSS"}]
        st = er.realized_stats(rows, 0.30)
        self.assertEqual(st["n"], 2)
        self.assertAlmostEqual(st["expectancy_pct"], -0.3, places=3)   # mean 0 minus cost
        self.assertEqual(st["graded"], {"WIN": 1, "LOSS": 1, "EXPIRED": 0})

    def test_empty_is_not_a_crash(self):
        self.assertEqual(er.realized_stats([], 0.3)["n"], 0)


class BootstrapTests(unittest.TestCase):
    def test_ci_brackets_the_mean_and_is_deterministic(self):
        pnls = [5.0] * 50 + [-5.0] * 100 + [0.0] * 50
        lo1, hi1 = er.bootstrap_ci(pnls, 0.0, iters=300)
        lo2, hi2 = er.bootstrap_ci(pnls, 0.0, iters=300)
        self.assertEqual((lo1, hi1), (lo2, hi2))          # fixed seed
        mean = sum(pnls) / len(pnls)
        self.assertLessEqual(lo1, mean)
        self.assertGreaterEqual(hi1, mean)

    def test_positive_edge_has_positive_lower_bound(self):
        lo, hi = er.bootstrap_ci([5.0] * 200, 0.0, iters=200)
        self.assertGreater(lo, 0.0)

    def test_flat_edge_has_lower_bound_at_or_below_zero(self):
        lo, _ = er.bootstrap_ci([5.0, -5.0] * 100, 0.0, iters=300)
        self.assertLessEqual(lo, 0.0)

    def test_tiny_sample_returns_none(self):
        self.assertEqual(er.bootstrap_ci([1.0], 0.0), (None, None))
