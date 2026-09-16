import unittest

from partial_tp import TPPlan, TPState, hit_target


class PartialTPTests(unittest.TestCase):
    def test_allocations_and_idempotency(self):
        plan = TPPlan(30, 30, 40, True, 0)
        state = TPState(entry_qty=10, remaining_qty=10, sl=90)
        qty, event = hit_target(state, plan, "tp1", 100)
        self.assertAlmostEqual(qty, 3.0)
        self.assertAlmostEqual(state.remaining_qty, 7.0)
        self.assertTrue(state.tp1_hit)
        self.assertTrue(state.breakeven_activated)
        self.assertAlmostEqual(state.sl, 100.0)
        again, duplicate = hit_target(state, plan, "tp1", 100)
        self.assertEqual(again, 0.0)
        self.assertTrue(duplicate["already_hit"])

    def test_all_targets_consume_full_quantity(self):
        plan = TPPlan()
        state = TPState(entry_qty=5, remaining_qty=5)
        self.assertAlmostEqual(hit_target(state, plan, "tp1", 100)[0], 1.5)
        self.assertAlmostEqual(hit_target(state, plan, "tp2", 100)[0], 1.5)
        self.assertAlmostEqual(hit_target(state, plan, "tp3", 100)[0], 2.0)
        self.assertAlmostEqual(state.remaining_qty, 0.0)

    def test_invalid_allocation(self):
        with self.assertRaises(ValueError):
            TPPlan(50, 20, 20).validate()

    def test_breakeven_buffer(self):
        plan = TPPlan(30, 30, 40, True, 0.25)
        state = TPState(entry_qty=10, remaining_qty=10)
        hit_target(state, plan, "tp1", 200)
        self.assertAlmostEqual(state.sl, 200.5)


if __name__ == "__main__":
    unittest.main()
