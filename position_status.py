#!/usr/bin/env python3
"""Run one position-tracking cycle with human-readable Telegram trade status."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import position_state
import positions
import signal_history
import store
import trade_journal
from botutil import fmt_price, log, esc, env_float, telegram_msg, load_json, save_json


def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log("POSITION_STATUS", "config load error:", exc)
        return {}


def _cfg_num(cfg, key, env_name, default):
    return env_float(env_name, cfg.get(key, default))


def _status_due(position_id, now_ts, interval_minutes):
    state = load_json(positions.STATUS_FILE, {}) or {}
    last = float(state.get(position_id, 0) or 0)
    due = now_ts - last >= max(1.0, float(interval_minutes)) * 60.0
    if due:
        state[position_id] = now_ts
        save_json(positions.STATUS_FILE, state)
    return due


def _target_line(label, value, hit, closed=False):
    if closed:
        icon = "❌"
    else:
        icon = "✅" if hit else "⏳"
    return f"{icon} <b>{label}:</b> {fmt_price(value)}"


def _status_message(pos, price, terminal_event=None):
    entry = float(pos.get("entry") or 0)
    pct = (price / entry - 1) * 100 if entry else 0.0
    pnl_icon = "🟩" if pct >= 0 else "🟥"
    pnl_emoji = "🚀" if pct >= 0 else "🛑"
    coin = esc(pos.get("coin", "?"))

    if terminal_event == "sl":
        progress = "Stopped Out"
        sl_suffix = "💥 *(Stopped Out)*"
        target_closed = True
    elif terminal_event == "expired":
        progress = "Expired"
        sl_suffix = "⏰ *(Expired)*"
        target_closed = True
    elif pos.get("tp3_hit"):
        progress = "All Targets Hit"
        sl_suffix = "🛡️ *(Moved to Breakeven)*" if pos.get("sl_breakeven") else "⛔"
        target_closed = False
    elif pos.get("tp1_hit"):
        progress = "TP1 Hit — Waiting for TP2 / TP3"
        sl_suffix = "🛡️ *(Moved to Breakeven)*" if pos.get("sl_breakeven") else "⛔"
        target_closed = False
    else:
        progress = "Waiting for Targets"
        sl_suffix = "⛔"
        target_closed = False

    return (
        f"📊 <b>${coin} Trade Status</b>\n"
        f"<b>Current Status:</b> {progress}\n"
        f"{pnl_icon} <b>P&L:</b> {pct:+.2f}% {pnl_emoji}\n\n"
        f"<b>Entry:</b> {fmt_price(entry)} ➡️ <b>Now:</b> {fmt_price(price)}\n"
        f"<b>SL:</b> {fmt_price(pos.get('sl'))} {sl_suffix}\n\n"
        f"<b>🎯 Targets:</b>\n"
        f"{_target_line('TP1', pos.get('tp1'), bool(pos.get('tp1_hit')), target_closed)}\n"
        f"{_target_line('TP2', pos.get('tp2'), bool(pos.get('tp2_hit')), target_closed)}\n"
        f"{_target_line('TP3', pos.get('tp3'), bool(pos.get('tp3_hit')), target_closed)}"
    )


def _apply_tp(pos, event, price):
    key = f"{event}_hit"
    pos[key] = True
    pct = ((price / float(pos.get("entry") or 0)) - 1) * 100 if pos.get("entry") else 0.0
    store.position_event(event, pos["coin"], pos["entry"], price=price)
    trade_journal.record(
        "target", pos["position_id"], coin=pos["coin"], status=pos["status"],
        target=event, price=price, pct=round(pct, 4),
        tp1_hit=pos.get("tp1_hit"), tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"),
    )
    if event == "tp1" and not pos.get("sl_breakeven"):
        pos["sl"] = float(pos.get("entry") or 0)
        pos["sl_breakeven"] = True
        trade_journal.record(
            "breakeven", pos["position_id"], coin=pos["coin"], status=pos["status"],
            sl=pos["sl"], reason="TP1 hit",
        )


def _close(pos, status, price, now, outcome):
    entry = float(pos.get("entry") or 0)
    pos["status"] = status
    pos["close_ts"] = now.isoformat(timespec="seconds")
    pos["close_price"] = price
    pos["realized_pnl_pct"] = round(((price / entry) - 1) * 100 if entry else 0, 4)
    try:
        pos["realized_pnl_usdt"] = round(float(pos.get("notional_usdt") or 0) * pos["realized_pnl_pct"] / 100.0, 8)
    except (TypeError, ValueError):
        pos["realized_pnl_usdt"] = None
    store.position_event("close", pos["coin"], entry, price=price, outcome=status)
    trade_journal.record(
        "close", pos["position_id"], coin=pos["coin"], status=status, outcome=outcome,
        price=price, realized_pnl_pct=pos["realized_pnl_pct"],
        realized_pnl_usdt=pos.get("realized_pnl_usdt"), notional_usdt=pos.get("notional_usdt"),
        tp1_hit=pos.get("tp1_hit"), tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"),
        exchange=pos.get("exchange"), order_id=pos.get("order_id"), mode=pos.get("mode", "paper"),
    )


def _send(token, chat_id, message):
    if not message:
        return
    try:
        telegram_msg(token, chat_id, message)
    except Exception as exc:
        log("POSITION_STATUS", "telegram error:", exc)


def run_cycle(token, chat_id, cfg):
    migrated = position_state.migrate_positions()
    if migrated:
        log("POSITION_STATUS", f"migrated {migrated} legacy position field(s)")

    positions_list = positions.load()
    if not positions_list:
        return 0

    expiry_days = _cfg_num(cfg, "position_expiry_days", "POSITION_EXPIRY_DAYS", 14.0)
    status_interval = _cfg_num(cfg, "position_status_interval_minutes", "POSITION_STATUS_INTERVAL_MINUTES", 15.0)
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    keep = []
    emitted = 0

    for pos in positions_list:
        if pos.get("status") != "open":
            continue
        coin = pos.get("coin", "")
        price = positions._public_price(pos.get("exchange") or "BINANCE", coin)
        if price is None:
            keep.append(pos)
            continue

        pos["last_price"] = price
        entry = float(pos.get("entry") or 0)
        pos["last_pct"] = round((price / entry - 1) * 100 if entry else 0, 2)
        try:
            entry_t = datetime.fromisoformat(pos["entry_ts"])
        except (TypeError, ValueError):
            entry_t = now
        age_days = (now - entry_t).total_seconds() / 86400

        if age_days >= expiry_days:
            pos["expiry_days"] = round(age_days, 1)
            _close(pos, "expired", price, now, "EXPIRED")
            _send(token, chat_id, _status_message(pos, price, "expired"))
            emitted += 1
            signal_history.record_outcome(pos, "EXPIRED", price)
            continue

        if price <= float(pos.get("sl") or 0):
            _close(pos, "closed_sl", price, now, "LOSS")
            _send(token, chat_id, _status_message(pos, price, "sl"))
            emitted += 1
            trade_journal.record("stop", pos["position_id"], coin=coin, price=price, outcome="LOSS")
            signal_history.record_outcome(pos, "LOSS", price)
            continue

        for event, level in (("tp1", pos.get("tp1")), ("tp2", pos.get("tp2")), ("tp3", pos.get("tp3"))):
            if level is None:
                continue
            if price >= float(level) and not pos.get(f"{event}_hit"):
                _apply_tp(pos, event, price)
                if event == "tp3":
                    _close(pos, "closed_tp3", price, now, "WIN")
                    _send(token, chat_id, _status_message(pos, price))
                    emitted += 1
                    signal_history.record_outcome(pos, "WIN", price)
                    break
                _send(token, chat_id, _status_message(pos, price))
                emitted += 1

        if pos.get("status") == "open":
            if _status_due(pos["position_id"], now_ts, status_interval):
                _send(token, chat_id, _status_message(pos, price))
                emitted += 1
            keep.append(pos)

    positions.save(keep)
    return emitted


def main() -> int:
    cfg = load_config()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    try:
        alerts = run_cycle(token, chat_id, cfg)
        log("POSITION_STATUS", f"cycle complete; emitted {alerts} status/lifecycle message(s)")
        return 0
    except Exception as exc:
        log("POSITION_STATUS", "cycle failed:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
