import unittest
from unittest.mock import patch

from execution_engine import ExecutionEngine


class ExecutionGateTests(unittest.TestCase):
    def test_engine_requires_both_gates(self):
        with patch.dict("os.environ", {"EXECUTION_ENABLED":"true"}):
            engine = ExecutionEngine({"execution_live_enabled": False})
        self.assertFalse(engine.enabled)

    def test_engine_can_enable_only_with_both_gates(self):
        with patch.dict("os.environ", {"EXECUTION_ENABLED":"true"}):
            engine = ExecutionEngine({"execution_live_enabled": True})
        self.assertTrue(engine.enabled)


if __name__ == "__main__":
    unittest.main()
