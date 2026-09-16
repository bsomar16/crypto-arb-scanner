import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from execution_recovery import ExecutionSafety, recover_active_intents


class ExecutionRecoveryTests(unittest.TestCase):
    def cfg(self):
        return {
            "realtime_min_net_pct": 0.5,
            "execution_confirmation_ttl_ms": 30000,
            "execution_max_notional_usdt": 300,
            "execution_max_active_intents": 1,
            "execution_max_daily_notional_usdt": 1000,
        }

    def opp(self, n=250):
        return SimpleNamespace(symbol="BTCUSDT", buy_exchange="binance", sell_exchange="bybit",
                               executable_notional_usdt=n, buy_ask=100000, sell_bid=101000,
                               net_pct=0.8, transfer_required=True, network="TRX")

    def test_revalidation_is_required_when_callback_supplied(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine(self.cfg(), d)
            intent = e.create_intent(self.opp())
            with self.assertRaises(ValueError):
                e.confirm(intent, True, revalidator=lambda _: False)

    def test_live_mode_requires_revalidator(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("EXECUTION_ENABLED")
            os.environ["EXECUTION_ENABLED"] = "true"
            try:
                e = ExecutionEngine(self.cfg(), d)
                intent = e.create_intent(self.opp())
                with self.assertRaises(PermissionError):
                    e.confirm(intent, True)
            finally:
                if old is None:
                    os.environ.pop("EXECUTION_ENABLED", None)
                else:
                    os.environ["EXECUTION_ENABLED"] = old

    def test_final_adapter_revalidation_gate(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("EXECUTION_ENABLED")
            os.environ["EXECUTION_ENABLED"] = "true"
            try:
                e = ExecutionEngine(self.cfg(), d)
                intent = e.create_intent(self.opp())
                intent = e.confirm(intent, True, revalidator=lambda _: True)
                self.assertEqual(intent.status, "READY_FOR_ADAPTER")
                with self.assertRaises(ValueError):
                    e.revalidate_before_adapter(intent, lambda _: False)
            finally:
                if old is None:
                    os.environ.pop("EXECUTION_ENABLED", None)
                else:
                    os.environ["EXECUTION_ENABLED"] = old

    def test_idempotency_returns_existing_active_intent(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine(self.cfg(), d)
            a = e.create_intent(self.opp())
            b = e.create_intent(self.opp())
            self.assertEqual(a.id, b.id)

    def test_transition_rejects_invalid_state_jump(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine(self.cfg(), d)
            intent = e.create_intent(self.opp())
            with self.assertRaises(ValueError):
                e.transition(intent, "COMPLETED")

    def test_restart_recovers_active_intents(self):
        with tempfile.TemporaryDirectory() as d:
            e = ExecutionEngine(self.cfg(), d)
            intent = e.create_intent(self.opp())
            recovered = recover_active_intents(str(Path(d) / "execution_intents.jsonl"))
            self.assertIn(intent.id, recovered)

    def test_kill_switch_blocks_execution(self):
        safety = ExecutionSafety({"execution_max_notional_usdt": 300}, tempfile.mkdtemp())
        safety.kill_switch = True
        with self.assertRaises(PermissionError):
            safety.assert_allowed(100, 0, 0)


if __name__ == "__main__":
    unittest.main()
