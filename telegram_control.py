#!/usr/bin/env python3
"""Telegram notification + explicit confirmation control plane.

This module never treats a notification as authorization. A callback must carry
an intent id and an explicit CONFIRM action; the caller remains responsible for
calling ExecutionEngine.confirm() and the exchange adapter.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional


class TelegramControl:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        self.base = f"https://api.telegram.org/bot{self.token}"

    def _call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(self.base + "/" + method, data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result.get('description')}")
        return result["result"]

    def send_opportunity(self, intent_id: str, text: str, *, chat_id: Optional[str] = None) -> Dict[str, Any]:
        target = chat_id or self.chat_id
        if not target:
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured")
        keyboard = {
            "inline_keyboard": [[
                {"text": "CONFIRM", "callback_data": f"arb:confirm:{intent_id}"},
                {"text": "CANCEL", "callback_data": f"arb:cancel:{intent_id}"},
            ]]
        }
        return self._call("sendMessage", {
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard, separators=(",", ":")),
        })

    def poll(self, handler: Callable[[str, str], None], *, timeout: int = 20) -> None:
        """Long-poll Telegram callbacks; handler(action, intent_id) must enforce confirmation."""
        offset = 0
        while True:
            result = self._call("getUpdates", {
                "timeout": timeout,
                "offset": offset,
                "allowed_updates": json.dumps(["callback_query"]),
            })
            for update in result:
                offset = int(update["update_id"]) + 1
                callback = update.get("callback_query") or {}
                data = str(callback.get("data", ""))
                parts = data.split(":")
                if len(parts) != 3 or parts[0] != "arb" or parts[1] not in {"confirm", "cancel"}:
                    continue
                handler(parts[1], parts[2])
                callback_id = callback.get("id")
                if callback_id:
                    self._call("answerCallbackQuery", {"callback_query_id": callback_id,
                                                        "text": "Recorded. Execution gate will revalidate the intent."})
            time.sleep(0.1)
