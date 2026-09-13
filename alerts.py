#!/usr/bin/env python3
"""Price alerts: manual thresholds + auto support/resistance, persisted state."""

from datetime import datetime, timezone
from botutil import esc, load_json, save_json
from markets import binance_klines, binance_price, yahoo_chart, yahoo_quote
import indicators as ind


def daily_levels(symbol, market="crypto", bars=45):
    """Recent support/resistance + ATR from daily bars."""
    try:
        if market == "crypto":
            closes, highs, lows, vols = binance_klines(symbol, "1d", bars)
        else:
            c = yahoo_chart(symbol, "1d", "6mo")
            if not c:
                return None
            closes, highs, lows = c["close"], c["high"], c["low"]
        if len(closes) < 30:
            return None
        r30 = max(highs[-30:])
        s30 = min(lows[-30:])
        r20 = max(highs[-20:])
        s20 = min(lows[-20:])
        a = ind.atr(highs, lows, closes)
        return {"r": r30, "s": s30, "r20": r20, "s20": s20,
                "atr": a if a else (r30 - s30) / 30.0}
    except Exception:
        return None


def current_price(symbol, market="crypto"):
    if market == "crypto":
        return binance_price(symbol)
    return yahoo_quote(symbol)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_price_alerts(cfg, state_path="state/price_state.json"):
    """Return (fired_lines, changed_bool). Monitors watchlist + holdings + manual."""
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    fired = []
    changed = False

    monitored = set()
    for w in cfg.get("watchlist", []):
        monitored.add((str(w).upper(), "crypto"))
    for h in cfg.get("holdings", []):
        sym = str(h.get("symbol", "")).upper()
        if sym:
            monitored.add((sym, h.get("market", "crypto")))
    for ad in cfg.get("price_alerts", []):
        sym = str(ad.get("symbol", "")).upper()
        if sym:
            monitored.add((sym, ad.get("market", "crypto")))

    for sym, market in sorted(monitored):
        price = current_price(sym, market)
        if price is None:
            continue
        key = f"{market}:{sym}"
        st = state.get(key, {})
        today_s = today()
        lv = daily_levels(sym, market)
        if lv:
            if price >= lv["r"] * 0.999:
                if st.get("r_fired") != today_s:
                    fired.append(
                        f"   🚀 <b>{esc(sym)}</b> broke ABOVE resistance "
                        f"${lv['r']:,.4g} (R30) · now ${price:,.4g}")
                    st["r_fired"] = today_s
                    changed = True
            if price <= lv["s"] * 1.001:
                if st.get("s_fired") != today_s:
                    fired.append(
                        f"   🩸 <b>{esc(sym)}</b> broke BELOW support "
                        f"${lv['s']:,.4g} (S30) · now ${price:,.4g}")
                    st["s_fired"] = today_s
                    changed = True
            if lv["r20"] and price >= lv["r20"] * 0.999 and \
                    lv["r"] and price < lv["r"] * 0.999:
                if st.get("r20_fired") != today_s:
                    fired.append(
                        f"   👀 <b>{esc(sym)}</b> at resistance "
                        f"${lv['r20']:,.4g} (R20) · watch for breakout")
                    st["r20_fired"] = today_s
                    changed = True
            if lv["s20"] and price <= lv["s20"] * 1.001 and \
                    lv["s"] and price > lv["s"] * 1.001:
                if st.get("s20_fired") != today_s:
                    fired.append(
                        f"   🧲 <b>{esc(sym)}</b> at support "
                        f"${lv['s20']:,.4g} (S20) · watch for bounce")
                    st["s20_fired"] = today_s
                    changed = True
        state[key] = {k: v for k, v in st.items()}

    # manual threshold alerts
    for ad in cfg.get("price_alerts", []):
        sym = str(ad.get("symbol", "")).upper()
        market = ad.get("market", "crypto")
        price = current_price(sym, market)
        if price is None:
            continue
        key = f"manual:{market}:{sym}:{ad.get('direction')}:{ad.get('level')}"
        st = state.get(key, {})
        today_s = today()
        lvl = float(ad.get("level", 0))
        note = ad.get("note", "")
        hit = price >= lvl if ad.get("direction") == "above" else price <= lvl
        if hit and st.get("fired") != today_s:
            arrow = "📈" if ad.get("direction") == "above" else "📉"
            txt = f"   {arrow} <b>{esc(sym)}</b> {ad.get('direction')} " \
                  f"${lvl:,.4g} · now ${price:,.4g}"
            if note:
                txt += f" — {esc(note)}"
            fired.append(txt)
            st["fired"] = today_s
            changed = True
        state[key] = st

    if changed:
        save_json(state_path, state)
    return fired, changed