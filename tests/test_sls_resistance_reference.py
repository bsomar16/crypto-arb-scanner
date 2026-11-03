import unittest

from sls_engine import _delayed_highest


class SlsResistanceReferenceTests(unittest.TestCase):
    def test_delayed_highest_excludes_latest_six_bars(self):
        highs = list(range(1, 41))
        # At index 39, length 10, delay 6 => source bars 24..33 (1-based).
        self.assertEqual(_delayed_highest(highs, 10, 6, 39), 34)

    def test_delayed_highest_returns_none_during_warmup(self):
        self.assertIsNone(_delayed_highest([1, 2, 3, 4, 5], 5, 6, 4))


if __name__ == "__main__":
    unittest.main()
