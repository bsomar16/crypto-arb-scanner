import unittest

from sls_engine import _chronological_bos


class SlsChronologicalBosTests(unittest.TestCase):
    def test_pivot_is_unavailable_before_right_confirmation(self):
        opens = [9.0, 9.0, 9.0, 9.0, 10.0, 10.0]
        closes = [9.5, 9.5, 9.5, 11.0, 11.0, 11.0]
        # Pivot at bar 2 is confirmed only at bar 4 with pivot_len=2.
        self.assertEqual(_chronological_bos(opens, closes, [(2, 10.0)], 2), [4])

    def test_future_confirmed_pivot_cannot_create_earlier_bos(self):
        opens = [9.0, 9.0, 9.0, 9.0, 9.0, 9.0]
        closes = [9.5, 9.5, 12.0, 9.5, 9.5, 9.5]
        # The bar-2 pivot is only confirmed at bar 4; bar 2 must not see it.
        self.assertEqual(_chronological_bos(opens, closes, [(2, 10.0)], 2), [])


if __name__ == "__main__":
    unittest.main()
