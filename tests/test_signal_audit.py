import unittest
from signal_audit import SignalAudit

class SignalAuditTests(unittest.TestCase):
    def test_counts_are_thread_safe_and_snapshot_is_sorted(self):
        audit = SignalAudit()
        audit.reject("volume")
        audit.reject("entry_confirmation")
        audit.accept()
        self.assertEqual(audit.snapshot(), {
            "entry_confirmation": 1,
            "qualified": 1,
            "volume": 1,
        })

    def test_total_rejections_excludes_qualified(self):
        audit = SignalAudit()
        audit.reject("trend")
        audit.reject("trend")
        audit.accept()
        self.assertEqual(audit.total_rejections(), 2)

if __name__ == "__main__":
    unittest.main()
