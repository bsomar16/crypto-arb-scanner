#!/usr/bin/env python3
"""Crypto position tracking for paper signals and future live execution."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from botutil import fmt_price, log, esc, env_float, telegram_msg, load_json, save_json, http_json
import store
import signal_history
import trade_journal
import risk_engine

POS_FILE = "state/positions.json"
STATUS_FILE = "state/position_status.json"
POSITION_EXPIRY_DEFAULT = 14
MAX_OPEN_DEFAULT = 40
STATUS_INTERVAL_DEFAULT = 15.0


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
        mode=pos.get("mode", "paper"), exchange=pos.get("exchange"),
        notional_usdt=pos.get("notional_usdt"),
    )


def open_picks(picks, cfg, source="daily"):
    """Open virtual/paper long tracking records from confirmed signal picks."""
    t = thresholds(cfg)
    max_open = int(_cfg_num(cfg, "max_open_positions", "MAX_OPEN_POSITIONS", MAX_OPEN_DEFAULT))
    default_notional = max(0.0, _cfg_num(cfg, "default_position_notional_usdt", "DEFAULT_POSITION_NOTIONAL_USDT", 300.0))
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
        requested_notional = r.get("notional_usdt", default_notional)
        try:
            notional = float(requested_notional)
        except (TypeError, ValueError):
            notional = default_notional
        allowed, reason = risk_engine.check_new_position(positions, notional, cfg)
        if not allowed:
            log("RISK", f"blocked {coin}: {reason}")
            continue
        pos = {
            "position_id": _position_id(coin, source),
            "notification_enabled": True,
            "coin": coin, "entry": entry,
            "entry_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "sl_pct": max(0.0, 1 - sl / entry) if entry else 0.0,
            "tp1_pct": max(0.0, tp1 / entry - 1) if entry else 0.0,
            "tp2_pct": max(0.0, tp2 / entry - 1) if entry else 0.0,
            "tp3_pct": max(0.0, tp3 / entry - 1) if entry else 0.0,
            "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            "status": "open", "close_ts": None, "close_price": None,
            "realized_pnl_pct": None, "realized_pnl_usdt": None,
            "rating": r.get("rating", "BUY"), "score": r.get("score"), "potential_pct": r.get("potential_pct"),
            "risk_pct": r.get("risk_pct"), "rr": r.get("rr"),
            "setup_type": r.get("setup_type"), "interval": r.get("interval"),
            "source": source, "mode": "paper", "exchange": r.get("exchange"),
            "order_id": r.get("order_id"), "entry_fill_qty": r.get("entry_fill_qty"),
            "entry_fee": r.get("entry_fee"), "notional_usdt": notional,
            "last_price": entry, "last_pct": 0.0,
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
    """Attach actual execution metadata and rebase risk levels to the real fill."""
    positions = load()
    changed = False
    for pos in positions:
        if pos.get("position_id") != position_id:
            continue
        old_entry = float(pos.get("entry") or 0)
        if exchange is not None:
            pos["exchange"] = str(exchange).upper()
        if order_id is not None:
            pos["order_id"] = str(order_id)
        if fill_qty is not None:
            pos["entry_fill_qty"] = float(fill_qty)
            if float(pos.get("entry") or 0) > 0:
                pos["notional_usdt"] = float(fill_qty) * float(pos.get("entry") or 0)
        if fee is not None:
            pos["entry_fee"] = float(fee)
        if fill_price is not None and float(fill_price) > 0:
            new_entry = float(fill_price)
            pos["entry"] = new_entry
            pos["last_price"] = new_entry
            if old_entry > 0:
                pos["sl"] = new_entry * (1 - float(pos.get("sl_pct", 0.05)))
                pos["tp1"] = new_entry * (1 + float(pos.get("tp1_pct", 0.05)))
                pos["tp2"] = new_entry * (1 + float(pos.get("tp2_pct", 0.10)))
                pos["tp3"] = new_entry * (1 + float(pos.get("tp3_pct", 0.20)))
            if fill_qty is not None:
                pos["notional_usdt"] = float(fill_qty) * new_entry
        pos["mode"] = "live"
        changed = True
        trade_journal.record("entry_fill", position_id, coin=pos.get("coin"), status=pos.get("status"),
                             exchange=pos.get("exchange"), order_id=pos.get("order_id"),
                             fill_qty=pos.get("entry_fill_qty"), fill_price=pos.get("entry"), fee=pos.get("entry_fee"),
                             notional_usdt=pos.get("notional_usdt"), mode="live",
                             sl=pos.get("sl"), tp1=pos.get("tp1"), tp2=pos.get("tp2"), tp3=pos.get("tp3"))
        break
    if changed:
        save(positions)
    return changed


def _public_price(exchange, coin):
    ex = str(exchange or "BINANCE").upper()
    symbol = str(coin or "").upper().replace("/USDT", "").replace("-USDT", "")
    if not symbol:
        return None
    try:
        if ex == "BINANCE":
            return float(http_json(f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}USDT", timeout=8)["price"])
        if ex == "BYBIT":
            d = http_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}USDT", timeout=8)
            return float(d["result"]["list"][0]["lastPrice"])
        if ex == "OKX":
            d = http_json(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT", timeout=8)
            return float(d["data"][0]["last"])
        if ex == "BITGET":
            d = http_json(f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={symbol}USDT", timeout=8)
            return float(d["data"][0]["lastPr"])
        if ex == "MEXC":
            return float(http_json(f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol}USDT", timeout=8)["price"])
    except Exception as exc:
        log("POSITIONS", f"price error {ex} {symbol}:", exc)
    return None


def _fmt_msg(event, coin, pos, price):
    entry = pos["entry"]
    pct = (price / entry - 1) * 100 if entry else 0
    e = esc(coin); f = fmt_price
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
    trade_journal.record("target", pos["position_id"], coin=pos["coin"], status=pos["status"],
                         target=event, price=price, pct=round(pct, 4), tp1_hit=pos.get("tp1_hit"),
                         tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"))


def _close(pos, status, price, now, outcome):
    pos["status"] = status
    pos["close_ts"] = now.isoformat(timespec="seconds")
    pos["close_price"] = price
    pos["realized_pnl_pct"] = round(((price / pos["entry"]) - 1) * 100 if pos.get("entry") else 0, 4)
    try:
        pos["realized_pnl_usdt"] = round(float(pos.get("notional_usdt") or 0) * pos["realized_pnl_pct"] / 100.0, 8)
    except (TypeError, ValueError):
        pos["realized_pnl_usdt"] = None
    store.position_event("close", pos["coin"], pos["entry"], price=price, outcome=status)
    trade_journal.record("close", pos["position_id"], coin=pos["coin"], status=status,
                         outcome=outcome, price=price, realized_pnl_pct=pos["realized_pnl_pct"],
                         realized_pnl_usdt=pos.get("realized_pnl_usdt"), notional_usdt=pos.get("notional_usdt"),
                         tp1_hit=pos.get("tp1_hit"), tp2_hit=pos.get("tp2_hit"), tp3_hit=pos.get("tp3_hit"),
                         exchange=pos.get("exchange"), order_id=pos.get("order_id"), mode=pos.get("mode", "paper"))


def _status_due(position_id, now_ts, interval_minutes):
    state = load_json(STATUS_FILE, {}) or {}
    last = float(state.get(position_id, 0) or 0)
    due = now_ts - last >= max(1.0, float(interval_minutes)) * 60.0
    if due:
        state[position_id] = now_ts
        save_json(STATUS_FILE, state)
    return due


def _status_msg(pos, price):
    entry = float(pos.get("entry") or 0)
    pct = (price / entry - 1) * 100 if entry else 0.0
    e = esc(pos.get("coin", "?"))
    flags = []
    if pos.get("tp1_hit"): flags.append("TP1✓")
    if pos.get("tp2_hit"): flags.append("TP2✓")
    if pos.get("tp3_hit"): flags.append("TP3✓")
    progress = " · ".join(flags) if flags else "No TP hit"
    return (f"📊 <b>TRADE STATUS · {e}</b>\n"
            f"Entry: {fmt_price(entry)} · Now: {fmt_price(price)} · P&L: <b>{pct:+.2f}%</b>\n"
            f"SL: {fmt_price(pos.get('sl'))} · TP1: {fmt_price(pos.get('tp1'))} · TP2: {fmt_price(pos.get('tp2'))} · TP3: {fmt_price(pos.get('tp3'))}\n"
            f"Progress: {progress} · Mode: {esc(pos.get('mode','paper'))}")


def check_positions(token, chat_id, cfg):
    """Check tracked positions independently of signal scans and emit periodic status."""
    expiry_days = thresholds(cfg)["expiry_days"]
    status_interval = _cfg_num(cfg, "position_status_interval_minutes", "POSITION_STATUS_INTERVAL_MINUTES", STATUS_INTERVAL_DEFAULT)
    positions = load()
    if not positions:
        return 0
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    keep = []
    alerts = []
    for pos in positions:
        if pos.get("status") != "open":
            continue
        coin = pos.get("coin", "")
        price = _public_price(pos.get("exchange") or "BINANCE", coin)
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
        if price <= pos["sl"]:
            _close(pos, "closed_sl", price, now, "LOSS")
            alerts.append(_fmt_msg("sl", coin, pos, price))
            trade_journal.record("stop", pos["position_id"], coin=coin, price=price, outcome="LOSS")
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
            if _status_due(pos["position_id"], now_ts, status_interval):
                alerts.append(_status_msg(pos, price))
            keep.append(pos)
    save(keep)
    for msg in alerts:
        if msg:
            try:
                telegram_msg(token, chat_id, msg)
            except Exception as exc:
                log("POSITIONS", "telegram error:", exc)
    return len(alerts)
