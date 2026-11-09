#!/usr/bin/env python3
"""Crypto-only price alerts: support/resistance and manual thresholds."""

from datetime import datetime, timezone

from botutil import esc, load_json, save_json
from markets import binance_klines, binance_price
import indicators as ind


def daily_levels(symbol, bars=45):
    """Recent crypto support/resistance + ATR from Binance daily bars."""
    try:
        closes, highs, lows, _vols = binance_klines(symbol, "1d", bars)
        if len(closes) < 30:
            return None
        r30 = max(highs[-30:])
        s30 = min(lows[-30:])
        r20 = max(highs[-20:])
        s20 = min(lows[-20:])
        a = ind.atr(highs, lows, closes)
        return {
            "r": r30,
            "s": s30,
            "r20": r20,
            "s20": s20,
            "atr": a if a else (r30 - s30) / 30.0,
        }
    except Exception:
        return None


def current_price(symbol):
    return binance_price(symbol)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_price_alerts(cfg, state_path="state/price_state.json"):
    """Return (fired_lines, changed_bool). Crypto watchlist + holdings + manual alerts."""
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    fired = []
    changed = False

    monitored = set()
    for w in cfg.get("watchlist", []):
        monitored.add(str(w).upper())
    for h in cfg.get("holdings", []):
        sym = str(h.get("symbol", "")).upper()
        if sym:
            monitored.add(sym)
    for ad in cfg.get("price_alerts", []):
        sym = str(ad.get("symbol", "")).upper()
        if sym:
            monitored.add(sym)

    for sym in sorted(monitored):
        price = current_price(sym)
        if price is None:
            continue
        key = f"crypto:{sym}"
        st = state.get(key, {})
        today_s = today()
        lv = daily_levels(sym)
        if lv:
            if price >= lv["r"] * 0.999:
                if st.get("r_fired") != today_s:
                    fired.append(
                        f"   🚀 <b>{esc(sym)}</b> broke ABOVE resistance "
                        f"${lv['r']:,.4g} (R30) · now ${price:,.4g}"
                    )
                    st["r_fired"] = today_s
                    changed = True
            if price <= lv["s"] * 1.001:
                if st.get("s_fired") != today_s:
                    fired.append(
                        f"   🩸 <b>{esc(sym)}</b> broke BELOW support "
                        f"${lv['s']:,.4g} (S30) · now ${price:,.4g}"
                    )
                    st["s_fired"] = today_s
                    changed = True
            if lv["r20"] and price >= lv["r20"] * 0.999 and price < lv["r"] * 0.999:
                if st.get("r20_fired") != today_s:
                    fired.append(
                        f"   👀 <b>{esc(sym)}</b> at resistance "
                        f"${lv['r20']:,.4g} (R20) · watch for breakout"
                    )
                    st["r20_fired"] = today_s
                    changed = True
            if lv["s20"] and price <= lv["s20"] * 1.001 and price > lv["s"] * 1.001:
                if st.get("s20_fired") != today_s:
                    fired.append(
                        f"   🧲 <b>{esc(sym)}</b> at support "
                        f"${lv['s20']:,.4g} (S20) · watch for bounce"
                    )
                    st["s20_fired"] = today_s
                    changed = True
        state[key] = {k: v for k, v in st.items()}

    # Manual crypto threshold alerts.
    for ad in cfg.get("price_alerts", []):
        sym = str(ad.get("symbol", "")).upper()
        price = current_price(sym)
        if price is None:
            continue
        key = f"manual:crypto:{sym}:{ad.get('direction')}:{ad.get('level')}"
        st = state.get(key, {})
        today_s = today()
        lvl = float(ad.get("level", 0))
        note = ad.get("note", "")
        hit = price >= lvl if ad.get("direction") == "above" else price <= lvl
        if hit and st.get("fired") != today_s:
            arrow = "📈" if ad.get("direction") == "above" else "📉"
            txt = (
                f"   {arrow} <b>{esc(sym)}</b> {ad.get('direction')} "
                f"${lvl:,.4g} · now ${price:,.4g}"
            )
            if note:
                txt += f" — {esc(note)}"
            fired.append(txt)
            st["fired"] = today_s
            changed = True
        state[key] = st

    if changed:
        save_json(state_path, state)
    return fired, changed
