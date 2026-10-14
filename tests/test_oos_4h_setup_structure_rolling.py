import unittest

from oos_4h_setup_structure_rolling import CANDIDATES, setup_name


class SetupFieldAliasTests(unittest.TestCase):
    def test_prefers_setup_type_when_present(self):
        self.assertEqual(setup_name({"setup_type": "PULLBACK", "setup": "MOMENTUM"}), "PULLBACK")

    def test_falls_back_to_setup(self):
        self.assertEqual(setup_name({"setup": "PULLBACK"}), "PULLBACK")

    def test_pullback_candidates_match_canonical_setup_type(self):
        row = {
            "setup_type": "PULLBACK",
            "structure_score": 55.0,
            "expansion_state": "BASE",
        }
        self.assertTrue(CANDIDATES["pullback"](row))
        self.assertTrue(CANDIDATES["pullback_and_structure_ge_50"](row))
        self.assertTrue(CANDIDATES["pullback_and_base"](row))
        self.assertTrue(CANDIDATES["pullback_structure_and_base"](row))

    def test_non_pullback_does_not_match_pullback_candidates(self):
        row = {
            "setup_type": "MOMENTUM",
            "structure_score": 55.0,
            "expansion_state": "BASE",
        }
        for name in (
            "pullback",
            "pullback_and_structure_ge_50",
            "pullback_and_base",
            "pullback_structure_and_base",
        ):
            self.assertFalse(CANDIDATES[name](row), name)


if __name__ == "__main__":
    unittest.main()
