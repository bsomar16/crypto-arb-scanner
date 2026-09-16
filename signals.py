#!/usr/bin/env python3
"""Crypto signal engine: multi-timeframe scalp + small-trade setups."""

import indicators as ind
from botutil import http_json

BN = "https://data-api.binance.vision"


def rating(s):
    if s >= 80: return "VERY STRONG"
    if s >= 70: return "STRONG BUY"
    if s >= 55: return "BUY"
    if s >= 45: return "WATCH"
    return "AVOID"


def score_daily(dc, vol_ratio, chg24, fng_nudge=0.0):
    s = 0.0
    if dc["close"] > dc["e20"]: s += 15
    if dc["e20"] > dc["e50"]: s += 15
    r = dc["rsi"] or 50
    if 45 <= r <= 68: s += 15
    elif 35 <= r < 45 or 68 < r <= 75: s += 8
    elif 25 <= r < 35 or 75 < r <= 80: s += 3
    if r > 82: s -= 15
    if r < 28: s -= 10
    if dc["macd"] > dc["macd_sig"]: s += 12
    if dc["macd"] > 0: s += 8
    if vol_ratio >= 3: s += 15
    elif vol_ratio >= 2: s += 12
    elif vol_ratio >= 1.5: s += 9
    elif vol_ratio >= 1: s += 5
    elif vol_ratio >= 0.7: s += 2
    if 0 <= chg24 <= 10: s += 10
    elif 10 < chg24 <= 20: s += 6
    elif -5 <= chg24 < 0: s += 4
    elif chg24 > 30: s -= 6
    if dc["macd"] > 0 and dc["macd"] > dc["macd_sig"] and dc["close"] > dc["e20"]: s += 5
    s += fng_nudge
    return max(0.0, min(100.0, s))


def daily_indicators(closes):
    if len(closes) < 15: return None
    e12, e26 = ind.ema(closes, 12), ind.ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ind.ema(macd, 9)
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    return {"rsi": ind.rsi(closes), "macd": macd[-1], "macd_sig": sig[-1],
            "e20": e20[-1], "e50": e50[-1], "close": closes[-1]}


def analyze_coin_daily(coin, fng_nudge=0.0, min_bars=35, vol_map=None):
    try:
        data = http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval=1d&limit=70", timeout=15)
        if len(data) < min_bars: return None
        closes = [float(k[4]) for k in data][:-1]
        highs = [float(k[2]) for k in data][:-1]
        lows = [float(k[3]) for k in data][:-1]
        dc = daily_indicators(closes)
        if dc is None: return None
        t24 = http_json(f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT", timeout=15)
        chg24, qv = float(t24["priceChangePercent"]), float(t24["quoteVolume"])
        vols = [float(k[5]) for k in data][:-1]
        vol_x = 1.0
        if vol_map and coin in vol_map: vol_x = vol_map[coin] or 1.0
        elif len(vols) >= 12:
            last, avg = (vols[-1] + vols[-2]) / 2, sum(vols[-22:-2]) / len(vols[-22:-2])
            vol_x = last / avg if avg > 0 else 0
        s = score_daily(dc, vol_x, chg24, fng_nudge)
        a = ind.atr(highs, lows, closes)
        return {"coin": coin, "price": dc["close"], "rsi": round(dc["rsi"], 1),
                "vol_x": round(vol_x, 2), "chg": round(chg24, 2), "vol_x6": round(vol_x, 1),
                "atr": a, "score": round(s, 1), "rating": rating(s), "qv": qv / 1e6,
                "above_e20": dc["close"] > dc["e20"],
                "macd_bull": dc["macd"] > dc["macd_sig"] and dc["macd"] > 0}
    except Exception:
        return None


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0


def intraday_signal(coin, interval="1h", limit=160, min_vol_x=1.15,
                    min_hour_vol=0, chg24=None, min_potential_pct=5.0,
                    max_potential_pct=80.0, min_score=55, min_rr=1.5):
    """Detect a momentum setup and estimate its upside potential.

    Potential is a model projection, never a guarantee. Targets use ATR and
    recent structure; live price/liquidity must be rechecked before trading.
    """
    try:
        data = http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}", timeout=15)
        if len(data) < 70: return None
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        vols = [float(k[5]) for k in data]
        qvols = [float(k[7]) for k in data]
        if min_hour_vol and sum(qvols[-4:]) < min_hour_vol: return None

        e9, e20, e21 = ind.ema(closes, 9), ind.ema(closes, 20), ind.ema(closes, 21)
        m, sig, hist = ind.macd(closes)
        r = ind.rsi(closes, 14) or 50
        price = closes[-1]
        last_v = (vols[-1] + vols[-2]) / 2
        avg_v = sum(vols[:-2]) / len(vols[:-2])
        vol_ratio = last_v / avg_v if avg_v > 0 else 0.0
        if chg24 is None:
            t24 = http_json(f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT", timeout=12)
            chg24 = float(t24["priceChangePercent"])

        st = 0.0
        if e9[-1] > e21[-1]: st += 1.25
        if price > e20[-1]: st += 1.25
        if m[-1] > sig[-1]: st += 1.0 if hist[-1] > hist[-2] else 0.5
        if 48 <= r <= 72: st += 1.0
        elif 40 <= r < 48: st += 0.5
        elif r > 78: st -= 0.5
        if vol_ratio >= 1.5: st += 1.0
        elif vol_ratio >= 1.25: st += 0.5

        resistance = max(highs[-21:-1]) if len(highs) >= 21 else price
        a = ind.atr(highs, lows, closes) or price * 0.01
        stop_dist = max(1.2 * a, price * 0.008)
        stop = price - stop_dist
        structural_target = resistance if resistance > price else price + 2.5 * a
        volatility_target = price + 3.0 * a
        target = max(structural_target, volatility_target)
        potential = min(float(max_potential_pct), max(0.0, _pct(target, price)))
        if potential < min_potential_pct:
            target = price + max(3.0 * a, price * min_potential_pct / 100.0)
            potential = min(float(max_potential_pct), _pct(target, price))

        risk_pct = _pct(price, stop)
        rr = potential / risk_pct if risk_pct > 0 else 0.0
        score = min(100.0, st / 5.75 * 100.0)
        fires = (st >= 3.75 and vol_ratio >= min_vol_x and price > e20[-1]
                 and score >= min_score and potential >= min_potential_pct and rr >= min_rr)
        if not fires: return None

        t1 = price + stop_dist * 1.5
        t2 = price + stop_dist * 2.5
        return {"coin": coin, "interval": interval, "price": price,
                "rsi": round(r, 1), "vol_x": round(vol_ratio, 2),
                "chg24": round(chg24, 2), "st": round(st, 2), "score": round(score, 1),
                "entry": price, "stop": stop, "t1": t1, "t2": t2, "t3": target,
                "potential_pct": round(potential, 1), "risk_pct": round(risk_pct, 1),
                "rr": round(rr, 2), "atr": round(a, 4), "resistance": resistance,
                "e9_e21": e9[-1] > e21[-1], "macd_rising": hist[-1] > hist[-2]}
    except Exception:
        return None
