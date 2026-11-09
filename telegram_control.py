#!/usr/bin/env python3
"""Telegram notification + explicit confirmation control plane.

Notifications are never authorization. Confirmation callbacks are accepted only
from the configured chat and, when supplied, configured Telegram user IDs.
The caller must still invoke ExecutionEngine.confirm() and revalidate the intent.
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
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", ""))
        raw_users = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        self.allowed_user_ids = {x.strip() for x in raw_users.split(",") if x.strip()}
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
        target = str(chat_id or self.chat_id)
        if not target:
            raise RuntimeError("TELEGRAM_CHAT_ID is not configured")
        keyboard = {"inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"arb:confirm:{intent_id}"},
            {"text": "CANCEL", "callback_data": f"arb:cancel:{intent_id}"},
        ]]}
        return self._call("sendMessage", {
            "chat_id": target, "text": text, "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard, separators=(",", ":")),
        })


    def send_execution_intent(self, intent: Any, *, chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a compact execution-review message; buttons are never authorization by themselves."""
        text = (
            f"⚠️ <b>EXECUTION REVIEW | {intent.symbol.upper()}</b>\n"
            f"🟢 BUY: <b>{intent.buy_exchange.upper()}</b> @ {intent.buy_price}\n"
            f"🔴 SELL: <b>{intent.sell_exchange.upper()}</b> @ {intent.sell_price}\n"
            f"💰 Notional: <b>{intent.notional_usdt:.2f} USDT</b>\n"
            f"📈 Net spread: <b>{intent.net_pct:.2f}%</b>\n"
            f"🆔 <code>{intent.id}</code>\n\n"
            "Confirming here only requests execution review; the backend must "
            "revalidate the opportunity immediately before any adapter call."
        )
        return self.send_opportunity(intent.id, text, chat_id=chat_id)

    def callback_handler(self, engine: Any, revalidator: Callable[[Any], bool]) -> Callable[[str, str], None]:
        """Create a Telegram callback handler backed by ExecutionEngine."""
        def handle(action: str, intent_id: str) -> None:
            intent = engine.get_intent(intent_id)
            if intent is None:
                raise ValueError("unknown execution intent")
            if action == "cancel":
                engine.cancel(intent)
                return
            if action == "confirm":
                engine.confirm(intent, True, revalidator=revalidator)
                return
            raise ValueError("unsupported Telegram execution action")
        return handle

    def _authorized_callback(self, callback: Dict[str, Any]) -> bool:
        message = callback.get("message") or {}
        callback_chat = str((message.get("chat") or {}).get("id", ""))
        if not self.chat_id or callback_chat != self.chat_id:
            return False
        if self.allowed_user_ids:
            user_id = str((callback.get("from") or {}).get("id", ""))
            if user_id not in self.allowed_user_ids:
                return False
        return True

    def poll(self, handler: Callable[[str, str], None], *, timeout: int = 20) -> None:
        """Long-poll callbacks; unauthorized callbacks are ignored."""
        offset = 0
        while True:
            result = self._call("getUpdates", {
                "timeout": timeout, "offset": offset,
                "allowed_updates": json.dumps(["callback_query"]),
            })
            for update in result:
                offset = int(update["update_id"]) + 1
                callback = update.get("callback_query") or {}
                callback_id = callback.get("id")
                if not self._authorized_callback(callback):
                    if callback_id:
                        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Unauthorized"})
                    continue
                data = str(callback.get("data", ""))
                parts = data.split(":")
                if len(parts) != 3 or parts[0] != "arb" or parts[1] not in {"confirm", "cancel"}:
                    continue
                try:
                    handler(parts[1], parts[2])
                    callback_text = "Recorded. Backend revalidation is required before execution."
                except Exception:
                    callback_text = "Request rejected by execution safety controls."
                if callback_id:
                    self._call("answerCallbackQuery", {"callback_query_id": callback_id,
                                                        "text": callback_text})
            time.sleep(0.1)
