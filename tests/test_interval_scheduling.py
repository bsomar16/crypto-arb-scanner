import unittest
from datetime import datetime, timezone

from scanner import _interval_due


class MultiTimeframeIntervalSchedulingTests(unittest.TestCase):
    def test_4h_uses_elapsed_schedule_after_delayed_run(self):
        now = datetime(2026, 10, 3, 16, 22, tzinfo=timezone.utc)
        last = datetime(2026, 10, 3, 15, 5, tzinfo=timezone.utc).timestamp()
        self.assertTrue(
            _interval_due("4h", now=now, last_scan_ts=last, schedule_minutes={"4h": 60})
        )

    def test_4h_is_not_due_before_configured_cadence(self):
        now = datetime(2026, 10, 3, 15, 45, tzinfo=timezone.utc)
        last = datetime(2026, 10, 3, 15, 5, tzinfo=timezone.utc).timestamp()
        self.assertFalse(
            _interval_due("4h", now=now, last_scan_ts=last, schedule_minutes={"4h": 60})
        )

    def test_1d_uses_configured_240_minute_cadence(self):
        now = datetime(2026, 10, 3, 8, 1, tzinfo=timezone.utc)
        last = datetime(2026, 10, 3, 3, 55, tzinfo=timezone.utc).timestamp()
        self.assertTrue(
            _interval_due("1d", now=now, last_scan_ts=last, schedule_minutes={"1d": 240})
        )

    def test_first_run_keeps_long_horizon_slot_guard(self):
        now = datetime(2026, 10, 3, 13, 50, tzinfo=timezone.utc)
        self.assertFalse(_interval_due("4h", now=now, last_scan_ts=None))
        now = datetime(2026, 10, 3, 16, 5, tzinfo=timezone.utc)
        self.assertTrue(_interval_due("4h", now=now, last_scan_ts=None))

    def test_fast_intervals_remain_due_every_workflow_run(self):
        now = datetime(2026, 10, 3, 13, 50, tzinfo=timezone.utc)
        self.assertTrue(_interval_due("5m", now=now, last_scan_ts=None))
        self.assertTrue(_interval_due("15m", now=now, last_scan_ts=None))


if __name__ == "__main__":
    unittest.main()
