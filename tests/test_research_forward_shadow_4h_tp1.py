import unittest
from research_forward_shadow_4h_tp1 import CANDIDATES, HOLD_MAX_HOURS, candidate_matches, research_readiness

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

class TP1ReadinessTests(unittest.TestCase):
    def test_research_readiness_requires_sample_and_matches_baseline(self):
        summaries={
            "baseline":{"closed_decisive":20,"sample_ready":True,"precision_pct":60.0},
            "structure_ge_50":{"closed_decisive":20,"sample_ready":True,"precision_pct":65.0},
            "base_and_structure_ge_50":{"closed_decisive":19,"sample_ready":False,"precision_pct":70.0},
        }
        readiness=research_readiness(summaries)
        self.assertTrue(readiness["structure_ge_50"]["forward_review_ready"])
        self.assertFalse(readiness["base_and_structure_ge_50"]["forward_review_ready"])
        self.assertEqual(readiness["structure_ge_50"]["delta_vs_baseline_pp"],5.0)

if __name__=="__main__":
    unittest.main()
