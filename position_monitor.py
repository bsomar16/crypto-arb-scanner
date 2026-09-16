#!/usr/bin/env python3
"""Persistent position monitor for fast TP/SL tracking."""
from __future__ import annotations

import argparse
import json
import os
import time

from botutil import log
from positions import check_positions


def _load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=None)
    args = parser.parse_args()

    cfg = _load_config()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    interval = args.interval or float(cfg.get("position_monitor_interval_seconds", 15))
    interval = max(5.0, interval)

    if not token or not chat_id:
        log("POSITIONS", "Telegram credentials are not configured; monitor will still track state")

    while True:
        try:
            alerts = check_positions(token, chat_id, cfg)
            if alerts:
                log("POSITIONS", f"monitor emitted {alerts} lifecycle alert(s)")
        except Exception as exc:
            log("POSITIONS", "monitor cycle error:", exc)
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
