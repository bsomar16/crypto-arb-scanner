#!/usr/bin/env python3
"""Wide-market crypto discovery and price-structure helpers.

The discovery layer is intentionally cheap: 24h ticker data narrows the Binance
USDT SPOT universe, then 1h + 15m acceleration finds candidates before expensive
multi-timeframe analysis.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from statistics import mean

import indicators as ind
from botutil import http_json

BN = "https://data-api.binance.vision"


def _klines(symbol: str, interval: str = "1h", limit: int = 32):
    try:
        d = http_json(
            f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}",
            timeout=12,
        )
        return d if len(d) >= 24 else None
    except Exception:
        return None


def _early_move_score(price_1h: float, price_6h: float, vol_x: float,
                      micro_change_pct: float, micro_vol_x: float,
                      relative: float, compression: float) -> float:
    """Score early expansion without requiring a large 24h move.

    This is a discovery/ranking score, never a trade signal or win probability.
    """
    score = 0.0
    if price_1h > 0:
        score += 14
    if price_6h > 0:
        score += 8
    if micro_change_pct > 0:
        score += min(14, micro_change_pct * 4)
    if micro_vol_x >= 1.15:
        score += min(16, (micro_vol_x - 1) * 16)
    if vol_x >= 1.2:
        score += min(12, (vol_x - 1) * 8)
    if relative > 0:
        score += min(10, relative * 0.7)
    if compression >= 0.15:
        score += min(10, compression * 25)
    return max(0.0, min(100.0, score))


def _fast_one(symbol: str, qv: float, chg24: float, btc_chg24: float):
    data = _klines(symbol)
    if not data:
        return None
    closed = data[:-1]
    if len(closed) < 20:
        return None
    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    vols = [float(k[5]) for k in closed]

    last = closes[-1]
    prev1 = closes[-2]
    prev6 = closes[-7]
    price_1h = (last / prev1 - 1) * 100 if prev1 else 0
    price_6h = (last / prev6 - 1) * 100 if prev6 else 0
    accel = price_1h - price_6h / 6.0

    recent_v = mean(vols[-2:])
    base_v = mean(vols[-22:-2]) if len(vols) >= 22 else mean(vols[:-2])
    vol_x = recent_v / base_v if base_v > 0 else 0

    recent_range = (max(highs[-6:]) - min(lows[-6:])) / last * 100 if last else 0
    prior_range = (max(highs[-18:-6]) - min(lows[-18:-6])) / last * 100 if last else 0
    compression = max(0.0, 1.0 - recent_range / prior_range) if prior_range > 0 else 0

    e9 = ind.ema(closes, 9)[-1]
    e20 = ind.ema(closes, 20)[-1]
    rsi = ind.rsi(closes, 14) or 50
    atr = ind.atr(highs, lows, closes) or last * 0.01
    atr_pct = atr / last * 100 if last else 0
    relative = chg24 - btc_chg24

    base_score = 0.0
    if e9 > e20:
        base_score += 18
    if price_1h > 0:
        base_score += 12
    if price_6h > 0:
        base_score += 10
    if relative > 0:
        base_score += min(12, relative * 0.8)
    if vol_x >= 1.2:
        base_score += min(18, (vol_x - 1) * 12)
    if compression >= 0.15:
        base_score += min(12, compression * 30)
    if 45 <= rsi <= 68:
        base_score += 10
    elif 68 < rsi <= 75:
        base_score += 5
    if 0 < atr_pct < 8:
        base_score += 5

    micro = _klines(symbol, "15m", 12)
    micro_change = 0.0
    micro_vol_x = 0.0
    if micro and len(micro) >= 8:
        mc = micro[:-1]
        mclose = [float(k[4]) for k in mc]
        mvol = [float(k[5]) for k in mc]
        if len(mclose) >= 2 and mclose[-2]:
            micro_change = (mclose[-1] / mclose[-2] - 1) * 100
        if len(mvol) >= 8:
            micro_base = mean(mvol[-8:-2])
            micro_recent = mean(mvol[-2:])
            micro_vol_x = micro_recent / micro_base if micro_base > 0 else 0.0

    early_score = _early_move_score(
        price_1h, price_6h, vol_x, micro_change, micro_vol_x, relative, compression
    )
    # Blend the new micro-acceleration evidence without allowing it to replace
    # the established 1h liquidity/trend context.
    score = 0.75 * min(100.0, base_score) + 0.25 * early_score

    return {
        "coin": symbol,
        "qv": qv,
        "chg24": chg24,
        "price_1h_pct": price_1h,
        "price_6h_pct": price_6h,
        "acceleration_pct": accel,
        "vol_x": vol_x,
        "compression": compression,
        "relative_strength": relative,
        "rsi": rsi,
        "micro_15m_change_pct": micro_change,
        "micro_15m_vol_x": micro_vol_x,
        "early_move_score": early_score,
        "score": max(0.0, min(100.0, score)),
    }


def discover(t24, cfg):
    """Return a broad, ranked discovery universe plus the final deep-scan pool."""
    rows = []
    for x in t24 or []:
        symbol = str(x.get("symbol", ""))
        if not symbol.endswith("USDT") or symbol == "USDTUSDT":
            continue
        coin = symbol[:-4]
        if any(q in coin for q in ("USDT", "FDUSD", "BUSD", "USDC", "USDP", "TUSD")):
            continue
        try:
            qv = float(x.get("quoteVolume", 0))
            chg = float(x.get("priceChangePercent", 0))
        except (TypeError, ValueError):
            continue
        if qv >= float(cfg.get("discovery_min_quote_volume", 500000)):
            rows.append((coin, qv, chg))

    rows.sort(key=lambda x: x[1], reverse=True)
    max_universe = int(cfg.get("discovery_universe_size", 600))
    fast_scan_size = max(1, min(max_universe, int(cfg.get("discovery_fast_scan_size", 300))))
    rows = rows[:fast_scan_size]
    btc_chg = next((r[2] for r in rows if r[0] == "BTC"), 0.0)

    workers = int(cfg.get("discovery_workers", 24))
    with ThreadPoolExecutor(max_workers=max(4, workers)) as ex:
        futures = [ex.submit(_fast_one, c, qv, chg, btc_chg) for c, qv, chg in rows]
        results = []
        for f in futures:
            result = f.result()
            if result:
                results.append(result)

    results.sort(key=lambda r: (-r["score"], -r["early_move_score"], -r["qv"]))
    deep_n = int(cfg.get("discovery_deep_candidates", 100))
    return results, results[:deep_n]


def structure_snapshot(closes, highs, lows):
    """Detect simple swing structure, BOS/CHOCH and liquidity levels."""
    if len(closes) < 30:
        return {"state": "UNKNOWN", "bos": False, "choch": False,
                "liquidity_sweep": False, "higher_lows": False,
                "compression": 0.0, "score": 0.0}

    def pivots(values, left=2, right=2, high=True):
        out = []
        for i in range(left, len(values) - right):
            window = values[i-left:i+right+1]
            v = values[i]
            if v == (max(window) if high else min(window)):
                out.append((i, v))
        return out

    ph = pivots(highs, high=True)
    pl = pivots(lows, high=False)
    last_ph = ph[-3:]
    last_pl = pl[-3:]

    higher_highs = len(last_ph) >= 2 and last_ph[-1][1] > last_ph[-2][1]
    higher_lows = len(last_pl) >= 2 and last_pl[-1][1] > last_pl[-2][1]
    if not higher_lows and len(lows) >= 8:
        earlier_low = min(lows[-8:-4])
        recent_low = min(lows[-4:])
        higher_lows = recent_low > earlier_low
    lower_highs = len(last_ph) >= 2 and last_ph[-1][1] < last_ph[-2][1]
    lower_lows = len(last_pl) >= 2 and last_pl[-1][1] < last_pl[-2][1]

    prior_high = max(highs[-13:-3])
    prior_low = min(lows[-13:-3])
    price = closes[-1]
    bos = price > prior_high
    sweep = lows[-3] < prior_low and price > prior_low
    bullish = higher_lows and (higher_highs or bos)
    bearish = lower_highs and lower_lows
    choch = sweep and bullish

    recent_range = (max(highs[-6:]) - min(lows[-6:])) / price if price else 0
    prior_range = (max(highs[-18:-6]) - min(lows[-18:-6])) / price if price else 0
    compression = max(0.0, 1.0 - recent_range / prior_range) if prior_range else 0.0

    score = 0.0
    if higher_lows:
        score += 18
    if higher_highs:
        score += 10
    if bos:
        score += 22
    if choch:
        score += 18
    if sweep:
        score += 15
    if compression > 0.15:
        score += min(12, compression * 30)
    if bearish:
        score -= 15

    state = "BULLISH" if bullish else ("BEARISH" if bearish else "MIXED")
    return {
        "state": state,
        "bos": bos,
        "choch": choch,
        "liquidity_sweep": sweep,
        "higher_lows": higher_lows,
        "higher_highs": higher_highs,
        "compression": round(compression, 4),
        "prior_high": prior_high,
        "prior_low": prior_low,
        "score": max(0.0, min(100.0, score)),
    }
