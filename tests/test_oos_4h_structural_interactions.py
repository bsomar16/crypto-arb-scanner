import unittest

from oos_4h_structural_interactions import setup


class TestOos4hStructuralInteractions(unittest.TestCase):
    def test_uses_canonical_setup_type(self):
        self.assertEqual(setup({"setup_type": "PULLBACK"}), "PULLBACK")

    def test_legacy_setup_fallback(self):
        self.assertEqual(setup({"setup": "PULLBACK"}), "PULLBACK")

    def test_canonical_field_wins_over_legacy(self):
        self.assertEqual(
            setup({"setup_type": "MOMENTUM", "setup": "PULLBACK"}),
            "MOMENTUM",
        )


if __name__ == "__main__":
    unittest.main()
