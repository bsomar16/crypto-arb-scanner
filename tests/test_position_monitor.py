import os
import sys
import unittest
from unittest.mock import patch

import position_monitor


class PositionMonitorTests(unittest.TestCase):
    @patch.object(position_monitor, "check_positions", return_value=1)
    @patch.object(position_monitor, "_load_config", return_value={"position_monitor_interval_seconds": 5})
    def test_once_runs_one_cycle(self, _cfg, check):
        old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_chat = os.environ.get("TELEGRAM_CHAT_ID")
        os.environ["TELEGRAM_BOT_TOKEN"] = "test"
        os.environ["TELEGRAM_CHAT_ID"] = "123"
        try:
            with patch.object(sys, "argv", ["position_monitor.py", "--once"]):
                self.assertEqual(position_monitor.main(), 0)
            check.assert_called_once_with("test", "123", {"position_monitor_interval_seconds": 5})
        finally:
            if old_token is None:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            else:
                os.environ["TELEGRAM_BOT_TOKEN"] = old_token
            if old_chat is None:
                os.environ.pop("TELEGRAM_CHAT_ID", None)
            else:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat


if __name__ == "__main__":
    unittest.main()
