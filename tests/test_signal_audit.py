import unittest
from signal_audit import SignalAudit


class SignalAuditTests(unittest.TestCase):
    def test_counts_are_thread_safe_and_snapshot_is_sorted(self):
        audit = SignalAudit()
        audit.reject("volume", interval="5m")
        audit.reject("entry_confirmation", interval="15m")
        audit.accept(interval="15m")
        self.assertEqual(audit.snapshot(), {
            "entry_confirmation": 1,
            "qualified": 1,
            "volume": 1,
        })

    def test_interval_and_setup_breakdown(self):
        audit = SignalAudit()
        audit.reject("volume", interval="5m", setup="PULLBACK")
        audit.reject("volume", interval="5m", setup="PULLBACK")
        audit.accept(interval="5m", setup="PULLBACK")
        self.assertEqual(audit.interval_snapshot()["5m"]["volume"], 2)
        self.assertEqual(audit.setup_snapshot()["PULLBACK"]["qualified"], 1)

    def test_summary_reports_qualification_rate(self):
        audit = SignalAudit()
        audit.reject("volume", interval="5m")
        audit.reject("entry_confirmation", interval="15m")
        audit.accept(interval="1h")
        summary = audit.summary(scans=3, hits=1, fresh=1, selected=1)
        self.assertEqual(summary["attempts"], 3)
        self.assertEqual(summary["qualified"], 1)
        self.assertEqual(summary["rejected"], 2)
        self.assertAlmostEqual(summary["qualification_rate_pct"], 33.33, places=2)

    def test_total_rejections_excludes_qualified(self):
        audit = SignalAudit()
        audit.reject("trend")
        audit.reject("trend")
        audit.accept()
        self.assertEqual(audit.total_rejections(), 2)


if __name__ == "__main__":
    unittest.main()
