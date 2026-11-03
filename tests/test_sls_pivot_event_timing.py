import unittest

from sls_engine import _is_new_confirmed_pivot


class SlsPivotEventTimingTests(unittest.TestCase):
    def test_pivot_event_occurs_only_on_right_confirmation_bar(self):
        self.assertFalse(_is_new_confirmed_pivot(10, 4, 13))
        self.assertTrue(_is_new_confirmed_pivot(10, 4, 14))
        self.assertFalse(_is_new_confirmed_pivot(10, 4, 15))


if __name__ == "__main__":
    unittest.main()
