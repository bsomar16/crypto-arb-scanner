import os
import unittest
from unittest.mock import patch

import position_status


class PositionStatusTests(unittest.TestCase):
    @patch.object(position_status, "check_positions", return_value=2)
    def test_main_runs_one_tracking_cycle(self, check):
        old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_chat = os.environ.get("TELEGRAM_CHAT_ID")
        os.environ["TELEGRAM_BOT_TOKEN"] = "test"
        os.environ["TELEGRAM_CHAT_ID"] = "123"
        try:
            self.assertEqual(position_status.main(), 0)
            check.assert_called_once_with("test", "123", {})
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
