import unittest
from research_forward_shadow_4h_tp1 import CANDIDATES, HOLD_MAX_HOURS, candidate_matches

class TP1ShadowTests(unittest.TestCase):
    def test_frozen_candidates(self):
        self.assertEqual(set(CANDIDATES), {
            "baseline","structure_ge_50","base_and_structure_ge_50","non_expansion_and_structure_ge_50"
        })

    def test_hold_window_is_frozen(self):
        self.assertEqual(HOLD_MAX_HOURS, 120)

    def test_candidate_predicates(self):
        signal={"setup_type":"MOMENTUM","structure_score":55,"expansion_state":"BASE"}
        self.assertTrue(candidate_matches(signal,"baseline"))
        self.assertTrue(candidate_matches(signal,"structure_ge_50"))
        self.assertTrue(candidate_matches(signal,"base_and_structure_ge_50"))
        self.assertTrue(candidate_matches(signal,"non_expansion_and_structure_ge_50"))

if __name__=="__main__":
    unittest.main()
