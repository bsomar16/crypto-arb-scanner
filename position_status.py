#!/usr/bin/env python3
"""Run one position-tracking cycle and emit Telegram lifecycle/status updates."""
from __future__ import annotations

import json
import os

from botutil import log
from positions import check_positions


def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log("POSITION_STATUS", "config load error:", exc)
        return {}


def main() -> int:
    cfg = load_config()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    try:
        alerts = check_positions(token, chat_id, cfg)
        log("POSITION_STATUS", f"cycle complete; emitted {alerts} alert/status message(s)")
        return 0
    except Exception as exc:
        log("POSITION_STATUS", "cycle failed:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
