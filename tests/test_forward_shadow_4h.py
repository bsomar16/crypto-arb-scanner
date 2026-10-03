import unittest

from research_forward_shadow_4h import (
    CANDIDATES,
    candidate_matches,
    resolve_position,
)


def bar(ts, open_, high, low, close):
    return [ts, open_, high, low, close, 1000, ts + 1, 100000]


class ForwardShadow4hTests(unittest.TestCase):
    def test_all_candidates_are_frozen_and_baseline_matches(self):
        signal = {
            "setup_type": "PULLBACK",
            "structure_score": 55,
            "expansion_state": "BASE",
        }
        self.assertEqual(len(CANDIDATES), 6)
        self.assertTrue(candidate_matches(signal, "baseline"))
        self.assertTrue(candidate_matches(signal, "pullback"))
        self.assertTrue(candidate_matches(signal, "pullback_and_structure_ge_50"))
        self.assertTrue(candidate_matches(signal, "pullback_and_base"))
        self.assertTrue(candidate_matches(signal, "pullback_structure_and_base"))
        self.assertTrue(candidate_matches(signal, "non_expansion_and_structure_ge_50"))

    def test_candidate_predicates_do_not_overlap_when_requirements_fail(self):
        signal = {
            "setup_type": "MOMENTUM",
            "structure_score": 60,
            "expansion_state": "EXPANSION",
        }
        self.assertTrue(candidate_matches(signal, "baseline"))
        for candidate in CANDIDATES[1:]:
            self.assertFalse(candidate_matches(signal, candidate))

    def test_stop_wins_when_stop_and_target_share_a_candle(self):
        position = {
            "position_id": "x",
            "candidate": "baseline",
            "status": "OPEN",
            "coin": "BTC",
            "candle_open_time": 1000,
            "entry": 100.0,
            "stop": 95.0,
            "t3": 110.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
        }
        result = resolve_position(position, [bar(2000, 100, 112, 94, 108)])
        self.assertIsNotNone(result)
        self.assertEqual(result["outcome"], "LOSS")
        self.assertEqual(result["exit_reason"], "STOP")

    def test_target_closes_before_max_hold(self):
        position = {
            "position_id": "x",
            "candidate": "baseline",
            "status": "OPEN",
            "coin": "BTC",
            "candle_open_time": 1000,
            "entry": 100.0,
            "stop": 95.0,
            "t3": 110.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
        }
        result = resolve_position(position, [bar(2000, 100, 111, 99, 109)])
        self.assertEqual(result["outcome"], "WIN")
        self.assertEqual(result["exit_reason"], "TP3")

    def test_position_remains_open_without_hit(self):
        position = {
            "position_id": "x",
            "candidate": "baseline",
            "status": "OPEN",
            "coin": "BTC",
            "candle_open_time": 1000,
            "entry": 100.0,
            "stop": 95.0,
            "t3": 110.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
        }
        result = resolve_position(position, [bar(2000, 100, 104, 98, 103)])
        self.assertEqual(result["status"], "OPEN")
        self.assertEqual(result["last_price"], 103.0)


if __name__ == "__main__":
    unittest.main()
