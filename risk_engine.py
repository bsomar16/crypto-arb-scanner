#!/usr/bin/env python3
"""Provider-neutral risk guard for SPOT position sizing and daily loss limits."""
from __future__ import annotations

from datetime import datetime, timezone

import trade_journal


def _num(cfg, key, default):
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def limits(cfg):
    return {
        "max_position_notional_usdt": max(0.0, _num(cfg, "max_position_notional_usdt", 300.0)),
        "max_total_exposure_usdt": max(0.0, _num(cfg, "max_total_exposure_usdt", 900.0)),
        "max_daily_loss_usdt": max(0.0, _num(cfg, "max_daily_loss_usdt", 30.0)),
        "risk_per_trade_pct": max(0.0, _num(cfg, "risk_per_trade_pct", 1.0)),
        "kill_switch": bool(cfg.get("risk_kill_switch", False)),
    }


def _day(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def open_positions(positions):
    return [p for p in positions if p.get("status") == "open"]


def total_exposure_usdt(positions):
    total = 0.0
    for p in open_positions(positions):
        value = p.get("notional_usdt")
        if value is None:
            try:
                value = float(p.get("entry_fill_qty") or 0) * float(p.get("entry") or 0)
            except (TypeError, ValueError):
                value = 0.0
        try:
            total += max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return total


def daily_realized_loss_usdt(rows=None, now=None):
    rows = trade_journal.read() if rows is None else rows
    today = (now or datetime.now(timezone.utc)).date()
    loss = 0.0
    for row in rows:
        if row.get("event") != "close" or row.get("outcome") != "LOSS":
            continue
        if _day(row.get("ts")) != today:
            continue
        pnl = row.get("realized_pnl_usdt")
        if pnl is not None:
            try:
                loss += max(0.0, -float(pnl))
                continue
            except (TypeError, ValueError):
                pass
        # Without a dollar P&L, deliberately do not invent one.
    return loss


def check_new_position(positions, notional_usdt, cfg, rows=None):
    """Return (allowed, reason). This is a hard guard, not a signal score."""
    lim = limits(cfg)
    if lim["kill_switch"]:
        return False, "risk kill switch enabled"
    try:
        notional = float(notional_usdt)
    except (TypeError, ValueError):
        return False, "invalid notional"
    if notional <= 0:
        return False, "notional must be positive"
    if notional > lim["max_position_notional_usdt"]:
        return False, "position notional exceeds limit"
    if total_exposure_usdt(positions) + notional > lim["max_total_exposure_usdt"]:
        return False, "total open exposure exceeds limit"
    if daily_realized_loss_usdt(rows) >= lim["max_daily_loss_usdt"]:
        return False, "daily realized loss limit reached"
    return True, "ok"


def stop_distance_pct(entry, stop):
    try:
        entry = float(entry)
        stop = float(stop)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (entry - stop) / entry * 100.0) if entry > 0 else 0.0


def suggested_notional(entry, stop, equity_usdt, cfg):
    """Size a long SPOT position from account risk, then cap it by hard limits."""
    lim = limits(cfg)
    try:
        equity = float(equity_usdt)
    except (TypeError, ValueError):
        return 0.0
    distance = stop_distance_pct(entry, stop) / 100.0
    if equity <= 0 or distance <= 0 or lim["risk_per_trade_pct"] <= 0:
        return 0.0
    raw = equity * (lim["risk_per_trade_pct"] / 100.0) / distance
    return round(max(0.0, min(raw, lim["max_position_notional_usdt"])), 8)
