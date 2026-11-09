#!/usr/bin/env python3
"""Crypto market-regime detection for spot signal context.

The regime layer describes the broader crypto environment. It does not create
signals and is intentionally bounded when used for ranking.
"""

from botutil import http_json
from indicators import atr, ema


def classify_regime(btc_closes, breadth_pct, atr_pct=None):
    """Classify a market regime from closed BTC prices and market breadth."""
    closes = [float(x) for x in btc_closes or [] if float(x) > 0]
    if len(closes) < 55:
        return {"state": "UNKNOWN", "btc_trend": "UNKNOWN", "breadth_pct": round(float(breadth_pct or 0), 1),
                "volatility": "UNKNOWN", "score_modifier": 0.0}

    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, 50)[-1]
    short = closes[-5]
    long = closes[-20]
    momentum = (short / long - 1.0) * 100.0 if long else 0.0
    trend_spread_pct = (e20 / e50 - 1.0) * 100.0 if e50 else 0.0
    breadth = float(breadth_pct or 0.0)
    vol = float(atr_pct or 0.0)

    if vol >= 4.0:
        volatility = "HIGH"
    elif vol <= 1.2:
        volatility = "LOW"
    else:
        volatility = "NORMAL"

    if closes[-1] > e20 > e50 and trend_spread_pct >= 0.25 and momentum >= 2.0 and breadth >= 55:
        state = "BULL_EXPANSION"
    elif closes[-1] > e20 and e20 > e50 and trend_spread_pct >= 0.25 and breadth >= 45:
        state = "BULLISH"
    elif closes[-1] < e20 < e50 and trend_spread_pct <= -0.25 and momentum <= -2.0 and breadth <= 45:
        state = "BEAR_EXPANSION"
    elif closes[-1] < e20 and e20 < e50 and trend_spread_pct <= -0.25 and breadth <= 55:
        state = "BEARISH"
    else:
        state = "SIDEWAYS"

    modifier = {
        "BULL_EXPANSION": 4.0,
        "BULLISH": 2.0,
        "SIDEWAYS": 0.0,
        "BEARISH": -2.0,
        "BEAR_EXPANSION": -4.0,
        "UNKNOWN": 0.0,
    }[state]
    return {
        "state": state,
        "btc_trend": "BULLISH" if closes[-1] > e20 > e50 else "BEARISH" if closes[-1] < e20 < e50 else "MIXED",
        "breadth_pct": round(breadth, 1),
        "volatility": volatility,
        "atr_pct": round(vol, 2),
        "momentum_20": round(momentum, 2),
        "score_modifier": modifier,
    }


def _btc_4h():
    data = http_json("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=4h&limit=100", timeout=15)
    closed = data[:-1] if len(data) > 1 else []
    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    if not closes:
        return [], 0.0
    a = atr(highs, lows, closes) or 0.0
    return closes, (a / closes[-1] * 100.0) if closes[-1] else 0.0


def detect_regime(t24, breadth_min_quote_volume=1000000, breadth_limit=100):
    """Build a current regime snapshot from Binance spot market data."""
    positive = 0
    eligible = 0
    for row in t24 or []:
        symbol = str(row.get("symbol", ""))
        if not symbol.endswith("USDT") or symbol == "USDTUSDT":
            continue
        try:
            qv = float(row.get("quoteVolume", 0) or 0)
            chg = float(row.get("priceChangePercent", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qv < breadth_min_quote_volume:
            continue
        coin = symbol[:-4]
        if coin in {"USDC", "FDUSD", "USDT", "DAI", "TUSD", "USDE", "BUSD"}:
            continue
        eligible += 1
        if chg > 0:
            positive += 1
        if eligible >= breadth_limit:
            break
    breadth = positive / eligible * 100.0 if eligible else 0.0
    closes, atr_pct = _btc_4h()
    return classify_regime(closes, breadth, atr_pct)
