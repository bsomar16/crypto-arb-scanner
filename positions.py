#!/usr/bin/env python3
"""Crypto position tracking for paper signals and future live execution.

A position is persisted independently from the 15-minute signal scanner. Each
TP/SL transition is journaled exactly once, so restart/retry runs do not create
duplicate lifecycle events. Live execution metadata can be attached later
without changing the virtual tracking model.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from botutil import fmt_price, log, esc, env_float, telegram_msg, load_json, save_json
from markets import binance_price
import store
import signal_history
import trade_journal

POS_FILE = "state/positions.json"
POSITION_EXPIRY_DEFAULT = 14
MAX_OPEN_DEFAULT = 40


def _cfg_num(cfg, key, env_name, default):
    return env_float(env_name, cfg.get(key, default))


def thresholds(cfg):
    return {
        "sl": _cfg_num(cfg, "stoploss_pct", "STOPLOSS_PCT", 0.05),
        "tp1": _cfg_num(cfg, "tp1_pct", "TP1_PCT", 0.05),
        "tp2": _cfg_num(cfg, "tp2_pct", "TP2_PCT", 0.10),
        "tp3": _cfg_num(cfg, "tp3_pct", "TP3_PCT", 0.20),
        "expiry_days": _cfg_num(cfg, "position_expiry_days", "POSITION_EXPIRY_DAYS", POSITION_EXPIRY_DEFAULT),
    }


def compute_levels(entry, t):
    return {"sl": entry * (1 - t["sl"]), "tp1": entry * (1 + t["tp1"]),
            "tp2": entry * (1 + t["tp2"]), "tp3": entry * (1 + t["tp3"])}


def load():
    data = load_json(POS_FILE, [])
    return data if isinstance(data, list) else []


def save(positions):
    save_json(POS_FILE, positions)


def _position_id(coin, source):
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{coin}-{source}-{uuid.uuid4().hex[:8]}"


def _journal_open(pos):
    trade_journal.record(
        "open", pos["position_id"], coin=pos["coin"], status="open",
        source=pos.get("source"), setup_type=pos.get("setup_type"),
        interval=pos.get("interval"), entry=pos["entry"], stop=pos["sl"],
        tp1=pos["tp1"], tp2=pos["tp2"], tp3=pos["tp3"],
        rating=pos.get("rating"), score=pos.get("score"),
        potential_pct=pos.get("potential_pct"), risk_pct=pos.get("risk_pct"), rr=pos.get("rr"),
        mode="paper",
    )


def open_picks(picks, cfg, source="daily"):
    """Open virtual/paper long tracking records from confirmed signal picks."""
    t = thresholds(cfg)
    max_open = int(_cfg_num(cfg, "max_open_positions", "MAX_OPEN_POSITIONS", MAX_OPEN_DEFAULT))
    positions = load()
    open_coins = {p["coin"] for p in positions if p.get("status") == "open"}
    opened = 0
    for r in picks:
        coin = r.get("coin", "")
        if not coin or coin in open_coins or len(open_coins) >= max_open:
            continue
        try:
            entry = float(r.get("entry", r["price"]))
        except (TypeError, KeyError, ValueError):
            continue
        fallback = compute_levels(entry, t)
        sl = float(r.get("stop", fallback["sl"]))
        tp1 = float(r.get("t1", fallback["tp1"]))
        tp2 = float(r.get("t2", fallback["tp2"]))
        tp3 = float(r.get("t3", fallback["tp3"]))
        signal_history.record_signal(r)
        pos = {
            "position_id": _position_id(coin, source),
            "coin": coin, "entry": entry,
            "entry_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "status": "open", "close_ts": None, "close_price": None,
            "realized_pnl_pct": None, "rating": r.get("rating", "BUY"),
            "score": r.get("score"), "potential_pct": r.get("potential_pct"),
            "risk_pct": r.get("risk_pct"), "rr": r.get("rr"),
            "setup_type": r.get("setup_type"), "interval": r.get("interval"),
            "source": source, "mode": "paper", "exchange": r.get("exchange"),
            "order_id": r.get("order_id"), "entry_fill_qty": r.get("entry_fill_qty"),
            "entry_fee": r.get("entry_fee"), "last_price": entry, "last_pct": 0.0,
        }
        positions.append(pos)
        open_coins.add(coin)
        opened += 1
        store.position_event("open", coin, entry, price=entry,
                             note=f"position_id={pos['position_id']};source={source};setup={r.get('setup_type','daily')}")
        _journal_open(pos)
    if opened:
        save(positions)
        log("POSITIONS", f"opened {opened} tracked paper positions ({source})")
    return opened


def attach_execution(position_id, exchange=None, order_id=None, fill_qty=None, fill_price=None, fee=None):
    """Attach actual execution metadata to an existing tracked position."""
    positions = load()
    changed = False
    for pos in positions:
        if pos.get("position_id") != position_id:
            continue
        if exchange is not None: pos["exchange"] = exchange
        if order_id is not None: pos["order_id"] = str(order_id)
        if fill_qty is not None: pos["entry_fill_qty"] = float(fill_qty)
        if fill_price is not None:
            pos["entry"] = float(fill_price)
            pos["last_price"] = float(fill_price)
        if fee is not None: pos["entry_fee"] = float(fee)
        pos["mode"] = "live"
        changed = True
        trade_journal.record("entry_fill", position_id, coin=pos.get("coin"), status=pos.get("status"),
                             exchange=pos.get("exchange"), order_id=pos.get("order_id"),
                             fill_qty=pos.get("entry_fill_qty"), fill_price=pos.get("entry"), fee=pos.get("entry_fee"), mode="live")
        break
    if changed:
        save(positions)
    return changed


def _fmt_msg(event, coin, pos, price):
    entry = pos["entry"]
    pct = (price / entry - 1) * 100 if entry else 0
    e = esc(coin)
    f = fmt_price
    if event == "sl": return f"🛑 STOP LOSS · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event == "tp1": return f"✅ TP1 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event == "tp2": return f"✅ TP2 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event == "tp3": return f"🏁 TP3 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%) · closed"
    if event == "expired": return f"⏰ <b>{e}</b> · position expired after {pos.get('expiry_days','?')}d · last {f(price)}"
    return None


def _record_target(pos, event, price):
    key = f"{event}_hit"
    pos[key] = True
    pct = ((price / pos["entry"]) - 1) * 100 if pos.get("entry") else 0
    store.position_event(event, pos["coin"], pos["entry"], price=price)
    trade_journal.record(event, pos["position_id"], coin=pos["coin"], status=pos["status"],
                         price=price, pct=round(pct, 4), tp1_hit=pos.get("tp1_hit"),
                         tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"))


def _close(pos, status, price, now, outcome):
    pos["status"] = status
    pos["close_ts"] = now.isoformat(timespec="seconds")
    pos["close_price"] = price
    pos["realized_pnl_pct"] = round(((price / pos["entry"]) - 1) * 100 if pos.get("entry") else 0, 4)
    store.position_event("close", pos["coin"], pos["entry"], price=price, outcome=status)
    trade_journal.record("close", pos["position_id"], coin=pos["coin"], status=status,
                         outcome=outcome, price=price, realized_pnl_pct=pos["realized_pnl_pct"],
                         tp1_hit=pos.get("tp1_hit"), tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"),
                         exchange=pos.get("exchange"), order_id=pos.get("order_id"), mode=pos.get("mode", "paper"))


def check_positions(token, chat_id, cfg):
    """Check tracked positions. Intended to run independently of signal scans."""
    expiry_days = thresholds(cfg)["expiry_days"]
    positions = load()
    if not positions:
        return 0
    now = datetime.now(timezone.utc)
    keep = []
    alerts = []
    for pos in positions:
        if pos.get("status") != "open":
            continue
        coin = pos.get("coin", "")
        price = binance_price(coin)
        if price is None:
            keep.append(pos)
            continue
        pos["last_price"] = price
        entry = float(pos["entry"])
        pos["last_pct"] = round((price / entry - 1) * 100 if entry else 0, 2)
        try:
            entry_t = datetime.fromisoformat(pos["entry_ts"])
        except (TypeError, ValueError):
            entry_t = now
        age_days = round((now - entry_t).total_seconds() / 86400, 1)
        if age_days >= expiry_days:
            pos["expiry_days"] = age_days
            _close(pos, "expired", price, now, "EXPIRED")
            alerts.append(_fmt_msg("expired", coin, pos, price))
            signal_history.record_outcome(pos, "EXPIRED", price)
            continue

        # Fail closed on a stop. If a single price update jumps through several
        # targets, record every crossed target instead of waiting for later scans.
        if price <= pos["sl"]:
            _close(pos, "closed_sl", price, now, "LOSS")
            alerts.append(_fmt_msg("sl", coin, pos, price))
            store.position_event("sl", coin, entry, price=price)
            signal_history.record_outcome(pos, "LOSS", price)
            continue

        for event, level in (("tp1", pos["tp1"]), ("tp2", pos["tp2"]), ("tp3", pos["tp3"])):
            if price >= level and not pos.get(f"{event}_hit"):
                _record_target(pos, event, price)
                alerts.append(_fmt_msg(event, coin, pos, price))
                if event == "tp3":
                    _close(pos, "closed_tp3", price, now, "WIN")
                    signal_history.record_outcome(pos, "WIN", price)
                    break
        if pos.get("status") == "open":
            keep.append(pos)

    save(keep)
    for msg in alerts:
        if msg:
            try:
                telegram_msg(token, chat_id, msg)
            except Exception as exc:
                log("POSITIONS", "telegram error:", exc)
    return len(alerts)
