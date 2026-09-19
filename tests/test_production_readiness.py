import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from production_readiness import audit_config, run_audit


class ProductionReadinessTests(unittest.TestCase):
    def test_baseline_config_is_complete_and_safe(self):
        cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
        report = audit_config(cfg)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["issues"], [])

    def test_live_activation_in_repository_config_fails_audit(self):
        cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
        cfg["execution_live_enabled"] = True
        report = audit_config(cfg)
        self.assertEqual(report["issues"], [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            result = run_audit(path)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("repository config" in x for x in result["issues"]))

    def test_dangerous_baseline_capabilities_are_rejected(self):
        cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
        cfg["execution_allow_market_orders"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            result = run_audit(path)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("market orders" in x for x in result["issues"]))

    def test_audit_is_runtime_environment_independent_for_baseline(self):
        with patch.dict("os.environ", {"EXECUTION_ENABLED": "true", "EXECUTION_KILL_SWITCH": "false"}):
            result = run_audit("config.json")
            self.assertEqual(result["status"], "PASS")

    def test_missing_config_is_audit_failure(self):
        result = run_audit("does-not-exist/config.json")
        self.assertEqual(result["status"], "FAIL")
