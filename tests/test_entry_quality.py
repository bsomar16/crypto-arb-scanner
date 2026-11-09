import unittest
from entry_engine import _body_strength

class EntryQualityTests(unittest.TestCase):
    def test_body_strength_is_bounded_for_normal_candle(self):
        self.assertAlmostEqual(_body_strength(100, 110, 99, 108), 8/11)

    def test_body_strength_handles_zero_range(self):
        self.assertEqual(_body_strength(100, 100, 100, 100), 0.0)

if __name__ == "__main__":
    unittest.main()
