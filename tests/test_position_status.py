import os
import unittest
from unittest.mock import patch

import position_status


class PositionStatusTests(unittest.TestCase):
    @patch.object(position_status, "run_cycle", return_value=2)
    @patch.object(position_status, "load_config", return_value={"position_status_interval_minutes": 15})
    def test_main_runs_one_tracking_cycle(self, _cfg, cycle):
        old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_chat = os.environ.get("TELEGRAM_CHAT_ID")
        os.environ["TELEGRAM_BOT_TOKEN"] = "test"
        os.environ["TELEGRAM_CHAT_ID"] = "123"
        try:
            self.assertEqual(position_status.main(), 0)
            cycle.assert_called_once_with("test", "123", {"position_status_interval_minutes": 15})
        finally:
            if old_token is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = old_token
            if old_chat is None:
                os.environ.pop("TELEGRAM_CHAT_ID", None)
            else:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat

    def _position(self):
        return {
            "position_id": "test-syn-1",
            "coin": "SYN",
            "entry": 0.1757,
            "sl": 0.1303,
            "tp1": 0.2210,
            "tp2": 0.2664,
            "tp3": 0.3017,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "status": "open",
        }

    def test_waiting_for_targets_format(self):
        msg = position_status._status_message(self._position(), 0.1874)
        self.assertIn("📊 <b>$SYN Trade Status</b>", msg)
        self.assertIn("+6.66%", msg)
        self.assertIn("⏳ <b>TP1:</b>", msg)
        self.assertIn("⏳ <b>TP2:</b>", msg)
        self.assertIn("⏳ <b>TP3:</b>", msg)
        self.assertIn("SL:", msg)

    def test_tp1_moves_stop_to_breakeven(self):
        pos = self._position()
        position_status._apply_tp(pos, "tp1", 0.2215)
        self.assertTrue(pos["tp1_hit"])
        self.assertTrue(pos["sl_breakeven"])
        self.assertAlmostEqual(pos["sl"], pos["entry"])
        msg = position_status._status_message(pos, 0.2215)
        self.assertIn("🛡️ *(Moved to Breakeven)*", msg)
        self.assertIn("✅ <b>TP1:</b>", msg)
        self.assertIn("⏳ <b>TP2:</b>", msg)
        self.assertIn("⏳ <b>TP3:</b>", msg)

    def test_stopped_out_format_marks_targets_closed(self):
        pos = self._position()
        pos["status"] = "closed_sl"
        msg = position_status._status_message(pos, 0.1303, "sl")
        self.assertIn("-25.84%", msg)
        self.assertIn("💥 *(Stopped Out)*", msg)
        self.assertEqual(msg.count("❌ <b>TP"), 3)


if __name__ == "__main__":
    unittest.main()
