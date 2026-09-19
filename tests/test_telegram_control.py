import os
import tempfile
import unittest
from types import SimpleNamespace

from execution_engine import ExecutionEngine
from telegram_control import TelegramControl


class TelegramExecutionLifecycleTests(unittest.TestCase):
    def opp(self):
        return SimpleNamespace(
            symbol="SOLUSDT", buy_exchange="binance", sell_exchange="bybit",
            executable_notional_usdt=100.0, buy_ask=100.0, sell_bid=106.0,
            net_pct=5.0, transfer_required=False, network=None,
        )

    def test_intent_lookup_and_cancel_are_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            engine = ExecutionEngine({}, d)
            intent = engine.create_intent(self.opp())
            self.assertEqual(engine.get_intent(intent.id).id, intent.id)
            cancelled = engine.cancel(intent)
            self.assertEqual(cancelled.status, "CANCELLED")

    def test_callback_handler_requires_backend_revalidation(self):
        with tempfile.TemporaryDirectory() as d:
            engine = ExecutionEngine({}, d)
            intent = engine.create_intent(self.opp())
            os.environ["TELEGRAM_BOT_TOKEN"] = "test"
            control = TelegramControl(chat_id="123")
            handler = control.callback_handler(engine, lambda _: True)
            handler("confirm", intent.id)
            self.assertEqual(engine.get_intent(intent.id).status, "DRY_RUN_CONFIRMED")

    def test_callback_handler_rejects_unknown_intent(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "test"
        control = TelegramControl(chat_id="123")
        engine = ExecutionEngine({}, tempfile.mkdtemp())
        handler = control.callback_handler(engine, lambda _: True)
        with self.assertRaises(ValueError):
            handler("confirm", "does-not-exist")


if __name__ == "__main__":
    unittest.main()
