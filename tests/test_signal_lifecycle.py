import os
import tempfile
import unittest
from unittest.mock import patch

import signal_lifecycle


class SignalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.addCleanup(lambda: os.path.exists(self.tmp.name) and os.unlink(self.tmp.name))
        self.patch = patch.object(signal_lifecycle, "PATH", self.tmp.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        signal_lifecycle._write({})

        self.signal = {
            "coin": "GALA",
            "interval": "15m",
            "setup_type": "MOMENTUM",
            "entry": 0.00183,
            "t1": 0.001875,
            "t2": 0.00192,
            "t3": 0.00195,
        }

    def test_register_then_activate(self):
        key = signal_lifecycle.register(self.signal)
        self.assertIn(key, signal_lifecycle.active_keys())
        row = signal_lifecycle.activate(self.signal)
        self.assertEqual(row["state"], "ACTIVE")

    def test_targets_advance_monotonically(self):
        signal_lifecycle.activate(self.signal)
        signal_lifecycle.record_target(self.signal, 1, 0.001875)
        self.assertEqual(signal_lifecycle._read()[signal_lifecycle.signal_key(self.signal)]["state"], "TP1")
        signal_lifecycle.record_target(self.signal, 2, 0.00192)
        self.assertEqual(signal_lifecycle._read()[signal_lifecycle.signal_key(self.signal)]["state"], "TP2")
        signal_lifecycle.record_target(self.signal, 3, 0.00195)
        self.assertEqual(signal_lifecycle._read()[signal_lifecycle.signal_key(self.signal)]["state"], "TP3")
        self.assertNotIn(signal_lifecycle.signal_key(self.signal), signal_lifecycle.active_keys())

    def test_stop_and_expiry_are_terminal(self):
        key = signal_lifecycle.register(self.signal)
        signal_lifecycle.close(key, "sl", 0.00178)
        self.assertEqual(signal_lifecycle._read()[key]["state"], "STOPPED")
        signal_lifecycle.close(key, "expired", 0.0018)
        self.assertEqual(signal_lifecycle._read()[key]["state"], "EXPIRED")
        self.assertNotIn(key, signal_lifecycle.active_keys())


if __name__ == "__main__":
    unittest.main()
