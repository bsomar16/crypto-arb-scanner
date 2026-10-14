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
