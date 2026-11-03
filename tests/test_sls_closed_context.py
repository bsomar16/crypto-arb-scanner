import unittest
from datetime import datetime, timezone

from sls_strategy import _asof_closed, _interval_close_ms


class SlsClosedContextTests(unittest.TestCase):
    def test_intraday_htf_bar_is_excluded_until_its_close(self):
        # 1h candle opens at 00:00 and closes at 01:00 UTC.
        row = {"t": 0, "c": 100}
        self.assertEqual(_asof_closed([row], 3_599_999, "1h"), [])
        self.assertEqual(_asof_closed([row], 3_600_000, "1h"), [row])

    def test_daily_htf_bar_uses_full_day_close(self):
        row = {"t": 0}
        self.assertEqual(_asof_closed([row], 86_399_999, "1d"), [])
        self.assertEqual(_asof_closed([row], 86_400_000, "1d"), [row])

    def test_monthly_close_is_calendar_aware_across_december(self):
        opened = int(datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
        expected = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(_interval_close_ms(opened, "1M"), expected)
        self.assertEqual(_asof_closed([{"t": opened}], expected - 1, "1M"), [])
        self.assertEqual(_asof_closed([{"t": opened}], expected, "1M"), [{"t": opened}])


if __name__ == "__main__":
    unittest.main()
