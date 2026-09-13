#!/usr/bin/env python3
"""Signal engine: daily scoring + intraday BUY signal detection."""

import indicators as ind
from botutil import http_json

BN = "https://data-api.binance.vision"


def rating(s):
    if s >= 75:
        return "STRONG BUY"
    if s >= 60:
        return "BUY"
    if s >= 50:
        return "WATCH"
    return "AVOID"


def score_daily(dc, vol_ratio, chg24, fng_nudge=0.0):
    """dc = dict with close/e20/e50/rsi/macd/macd_sig."""
    s = 0.0
    if dc["close"] > dc["e20"]:
        s += 15
    if dc["e20"] > dc["e50"]:
        s += 15
    r = dc["rsi"] or 50
    if 45 <= r <= 68:
        s += 15
    elif 35 <= r < 45 or 68 < r <= 75:
        s += 8
    elif 25 <= r < 35 or 75 < r <= 80:
        s += 3
    if r > 82:
        s -= 15
    if r < 28:
        s -= 10
    if dc["macd"] > dc["macd_sig"]:
        s += 12
    if dc["macd"] > 0:
        s += 8
    if vol_ratio >= 3:
        s += 15
    elif vol_ratio >= 2:
        s += 12
    elif vol_ratio >= 1.5:
        s += 9
    elif vol_ratio >= 1.0:
        s += 5
    elif vol_ratio >= 0.7:
        s += 2
    if 0 <= chg24 <= 10:
        s += 10
    elif 10 < chg24 <= 20:
        s += 6
    elif -5 <= chg24 < 0:
        s += 4
    elif chg24 > 30:
        s -= 6
    if dc["macd"] > 0 and dc["macd"] > dc["macd_sig"] and dc["close"] > dc["e20"]:
        s += 5
    s += fng_nudge
    return max(0.0, min(100.0, s))


def daily_indicators(closes):
    e12 = ind.ema(closes, 12)
    e26 = ind.ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ind.ema(macd, 9)
    e20 = ind.ema(closes, 20)
    e50 = ind.ema(closes, 50)
    return {"rsi": ind.rsi(closes), "macd": macd[-1], "macd_sig": sig[-1],
            "e20": e20[-1], "e50": e50[-1], "close": closes[-1]}


def analyze_coin_daily(coin, fng_nudge=0.0, min_bars=35):
    try:
        data = http_json(
            f"{BN}/api/v3/klines?symbol={coin}USDT&interval=1d&limit=70",
            timeout=15)
        if len(data) < min_bars:
            return None
        closes = [float(k[4]) for k in data][:-1]
        dc = daily_indicators(closes)
        t24 = http_json(
            f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT", timeout=15)
        chg24 = float(t24["priceChangePercent"])
        qv = float(t24["quoteVolume"])
        closes_h, highs_h, lows_h, vols_h = [], [], [], []
        try:
            kh = http_json(
                f"{BN}/api/v3/klines?symbol={coin}USDT&interval=1h&limit=25",
                timeout=15)
            vols_h = [float(k[5]) for k in kh]
            closes_h = [float(k[4]) for k in kh]
        except Exception:
            pass
        if len(vols_h) >= 12:
            last = (vols_h[-1] + vols_h[-2]) / 2
            avg = sum(vols_h[:-2]) / len(vols_h[:-2])
            vol_x = last / avg if avg > 0 else 0
        else:
            vol_x = 1.0
        s = score_daily(dc, vol_x or 1.0, chg24, fng_nudge)
        a = ind.atr(highs_h, lows_h, closes_h)
        return {"coin": coin, "price": dc["close"], "rsi": round(dc["rsi"], 1),
                "vol_x": round(vol_x, 2) if vol_x else 1.0,
                "chg": round(chg24, 2), "vol_x6": round(vol_x, 1),
                "atr": a, "score": round(s, 1), "rating": rating(s),
                "qv": qv / 1e6,
                "above_e20": dc["close"] > dc["e20"],
                "macd_bull": dc["macd"] > dc["macd_sig"] and dc["macd"] > 0}
    except Exception:
        return None


def intraday_signal(coin, interval="1h", limit=120, min_vol_x=1.25):
    """Detect a fresh momentum break on one symbol. Returns dict or None."""
    try:
        data = http_json(
            f"{BN}/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}",
            timeout=15)
        if len(data) < 60:
            return None
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        vols = [float(k[5]) for k in data]

        e9 = ind.ema(closes, 9)
        e20 = ind.ema(closes, 20)
        e21 = ind.ema(closes, 21)
        m, s, hist = ind.macd(closes)
        r = ind.rsi(closes, 14) or 50
        price = closes[-1]

        last_v = (vols[-1] + vols[-2]) / 2
        avg_v = sum(vols[:-2]) / len(vols[:-2])
        vol_ratio = last_v / avg_v if avg_v > 0 else 0.0

        t24 = http_json(f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT",
                        timeout=12)
        chg24 = float(t24["priceChangePercent"])

        st = 0.0
        if e9[-1] > e21[-1]:
            st += 1.0
        if price > e20[-1]:
            st += 1.0
        if m[-1] > s[-1]:
            st += 1.0 if hist[-1] > hist[-2] else 0.5
        if 48 <= r <= 72:
            st += 1.0
        elif 40 <= r < 48:
            st += 0.5
        elif r > 78:
            st -= 0.5
        if vol_ratio >= 1.5:
            st += 1.0
        elif vol_ratio >= 1.25:
            st += 0.5

        fires = st >= 4.0 and vol_ratio >= min_vol_x and price > e20[-1]
        if not fires:
            return None

        a = ind.atr(highs, lows, closes) or price * 0.01
        return {"coin": coin, "interval": interval, "price": price,
                "rsi": round(r, 1), "vol_x": round(vol_ratio, 2),
                "chg24": round(chg24, 2), "st": round(st, 1),
                "entry": price, "stop": price - 1.5 * a,
                "t1": price + a, "t2": price + 2 * a, "t3": price + 3 * a,
                "atr": round(a, 4),
                "e9_e21": e9[-1] > e21[-1], "macd_rising": hist[-1] > hist[-2]}
    except Exception:
        return None