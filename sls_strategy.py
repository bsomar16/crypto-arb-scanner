"""Native, long-only TradingView SLS port for Binance SPOT candles."""
from threading import RLock
import time
import indicators as ind
from botutil import http_json

INTERVALS = ("5m", "15m", "1h", "4h", "1d", "1w")
HTF_MAP = {"5m": "1h", "15m": "4h", "1h": "1d", "4h": "1w", "1d": "1M", "1w": None}
THRESHOLDS = {"Conservative": 90, "Balanced": 86, "Aggressive": 82}
_CACHE, _LOCK = {}, RLock()


def _candles(symbol, interval, limit=240):
    key = (symbol, interval, limit)
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] < 45:
            return item[1]
    try:
        raw = http_json(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}", timeout=12)
        # Binance returns a still-open final candle; never evaluate it.
        rows = raw[:-1]
        out = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
                "c": float(k[4]), "v": float(k[5]), "q": float(k[7])} for k in rows]
        value = out if len(out) >= 200 else None
    except Exception:
        value = None
    with _LOCK:
        _CACHE[key] = (now, value)
    return value


def _pivot_lows(values, left=2, right=2):
    result = []
    for p in range(left, len(values) - right):
        x = values[p]
        if all(x < values[j] for j in range(p-left, p)) and all(x <= values[j] for j in range(p+1, p+right+1)):
            result.append((p, x))
    return result


def _rsi_series(values, period=14):
    """Rolling RSI values aligned to candle indices (no forward references)."""
    out = [50.0] * len(values)
    for i in range(period, len(values)):
        window = [values[j] - values[j-1] for j in range(i-period+1, i+1)]
        gains = sum(max(x, 0.0) for x in window) / period
        losses = sum(max(-x, 0.0) for x in window) / period
        out[i] = 100.0 if losses == 0 and gains > 0 else 50.0 if losses == 0 else 100.0 - 100.0 / (1.0 + gains / losses)
    return out


def _state(rows):
    if not rows or len(rows) < 200:
        return None
    close = [x["c"] for x in rows]
    e20, e50, e200 = ind.ema(close, 20), ind.ema(close, 50), ind.ema(close, 200)
    macd, sig, hist = ind.macd(close)
    return {"close": close[-1], "closes": close, "e20": e20, "e50": e50, "e200": e200,
            "macd": macd, "sig": sig, "hist": hist, "rsi": float(ind.rsi(close, 14) or 50)}


def evaluate_sls_coin(coin, interval="1h", cfg=None, chg24=0.0, candle_provider=None):
    """Return a scanner-compatible BUY signal or None; no short-side path exists."""
    if interval not in INTERVALS:
        return None
    cfg = cfg or {}
    try:
        fetch = candle_provider or _candles
        rows = fetch(str(coin).upper(), interval)
        s = _state(rows)
        if not s:
            return None
        close, high, low = s["closes"], [x["h"] for x in rows], [x["l"] for x in rows]
        opn, vol, qvol = [x["o"] for x in rows], [x["v"] for x in rows], [x["q"] for x in rows]
        price = close[-1]
        atr = float(ind.atr(high, low, close) or 0)
        if atr <= 0 or price <= 0:
            return None

        # C1: EMA alignment, price position, slope and RSI (20 points).
        c1 = (8 if s["e20"][-1] > s["e50"][-1] > s["e200"][-1] else 0)
        c1 += 5 if price > s["e20"][-1] > s["e50"][-1] else 0
        c1 += 4 if s["e20"][-1] > s["e20"][-4] else 0
        c1 += 3 if 48 <= s["rsi"] <= 68 else 1 if 42 <= s["rsi"] < 48 else 0

        # C2: previous closed HTF candle and BTC benchmark (15 points).
        htf = HTF_MAP[interval]
        htf_ok, htf_state = interval == "1w", "SKIPPED" if interval == "1w" else "UNAVAILABLE"
        if htf:
            hs = _state(fetch(str(coin).upper(), htf, 220))
            if hs:
                htf_ok = hs["close"] > hs["e20"][-1] > hs["e50"][-1]
                htf_state = "BULLISH" if htf_ok else "NOT_BULLISH"
        bs = _state(fetch("BTC", interval, 220))
        btc_ok = bool(bs and bs["close"] > bs["e20"][-1] > bs["e50"][-1])
        c2 = (8 if htf_ok else 0) + (7 if btc_ok else 0)

        # C3: MACD, histogram slope and price ROC (15 points).
        c3 = (5 if s["macd"][-1] > s["sig"][-1] else 0)
        c3 += 4 if s["hist"][-1] > s["hist"][-2] else 0
        c3 += 3 if close[-1] > close[-11] else 0
        c3 += 3 if close[-1] > close[-4] else 0

        # C4: relative volume, bullish candle body and volume pressure (15).
        base = sum(vol[-22:-2]) / 20
        vol_x = (sum(vol[-2:]) / 2) / base if base > 0 else 0
        body = abs(close[-1] - opn[-1]) / max(high[-1] - low[-1], 1e-12)
        green = close[-1] > opn[-1]
        c4 = min(7, vol_x * 3.5) + (4 if green and body >= .45 else 0)
        c4 += 4 if close[-1] > close[-2] and vol[-1] >= vol[-2] else 0
        c4 = min(15, c4)

        # C5: confirmed structure proxies: breakout, higher lows, discount, sweep.
        prior_high = max(high[-21:-1])
        breakout = price > prior_high
        pivots = _pivot_lows(low)
        higher_lows = len(pivots) >= 2 and pivots[-1][1] > pivots[-2][1]
        discount = price <= s["e20"][-1] * 1.015
        prior_low = min(low[-10:-1])
        sweep = low[-1] < prior_low and price > prior_low
        c5 = (5 if breakout else 0) + (4 if higher_lows else 0) + (3 if discount else 0) + (3 if sweep else 0)

        # C6: bullish RSI divergence from confirmed swing lows (10).
        rsi_values = _rsi_series(close, 14)
        lp = _pivot_lows(low)
        divergence = (
            len(lp) >= 2
            and lp[-1][1] < lp[-2][1]
            and rsi_values[lp[-1][0]] > rsi_values[lp[-2][0]] + 2
        )
        c6 = 10 if divergence else 0

        # C7: extension guard, ATR stop and upside room (10).
        extension = (price - s["e20"][-1]) / atr
        extension_ok = extension <= 2.5
        stop = price - 1.5 * atr
        risk_pct = (price - stop) / price * 100
        room_pct = (max(prior_high, price + 3 * atr) / price - 1) * 100
        c7 = (4 if extension_ok else 0) + (3 if risk_pct <= 8 else 0) + (3 if room_pct >= 3 * risk_pct else 0)

        # C8: independent confluence (10).
        c8 = sum((2.5 if flag else 0) for flag in (breakout, higher_lows, htf_ok, vol_x >= 1.2))
        score = min(100.0, c1+c2+c3+c4+c5+c6+c7+c8)
        mode = str(cfg.get("sls_sensitivity", "Balanced")).title()
        threshold = float(cfg.get("sls_min_score", THRESHOLDS.get(mode, 86)))
        turnover = sum(qvol[-20:])
        liquidity_floor = 150000 if interval in ("5m", "15m", "1h", "4h") else 3000000

        # All gates are mandatory; absent BTC/HTF data fails closed.
        if not (score >= threshold and htf_ok and btc_ok and turnover >= liquidity_floor
                and extension_ok and green):
            return None

        risk = price - stop
        ladder = [price + risk * r for r in (1.5, 2.5, 4, 6, 8)]
        t1, t2, t3 = ladder[:3]
        potential = (t3 / price - 1) * 100
        return {
            "coin": str(coin).upper(), "interval": interval, "strategy": "SLS",
            "strategy_id": f"sls_{interval}", "strategy_family": "SLS",
            "trade_horizon": "Scalp" if interval in ("5m", "15m") else "Medium" if interval in ("1h", "4h") else "Long",
            "setup_type": "BREAKOUT" if breakout else "TREND_PULLBACK",
            "signal_action": "BUY", "candle_open_time": rows[-1]["t"],
            "price": price, "entry": price, "stop": stop, "t1": t1, "t2": t2, "t3": t3, "target": t3,
            "potential_pct": round(potential, 2), "risk_pct": round(risk_pct, 2),
            "rr": round(potential / risk_pct, 2) if risk_pct else 0,
            "atr": atr, "score": round(score, 1), "st": round(score, 1),
            "rsi": round(s["rsi"], 1), "vol_x": round(vol_x, 2), "chg24": float(chg24 or 0),
            "entry_quality": round(score, 1), "expansion_score": round(score, 1),
            "entry_trigger": "SLS_CONFIRMED_CLOSED_CANDLE", "bos_level": prior_high,
            "reasons": [f"SLS score {score:.1f}/100 (threshold {threshold:.0f})",
                        f"HTF {htf or 'none'}: {htf_state}", "BTC benchmark bullish",
                        f"volume x{vol_x:.2f}", "ATR stop 1.5x; targets 1.5R/2.5R/4R/6R/8R"],
            "trend_4h": htf_state, "trend_interval": htf or interval,
            "mtf_1h": "N/A", "mtf_1d": "N/A", "mtf_1w": "N/A",
            "sls_components": {"c1": c1, "c2": c2, "c3": c3, "c4": c4,
                               "c5": c5, "c6": c6, "c7": c7, "c8": c8},
            "sls_sensitivity": mode, "sls_threshold": threshold,
            "sls_htf": htf, "sls_btc_confirmed": btc_ok, "sls_quote_turnover_20": turnover,
            "estimated_hold_min_hours": None, "estimated_hold_max_hours": None,
            "risk_reward_ladder": ladder, "bullish_potential": {"tier": "SLS", "score": score},
        }
    except Exception:
        # Isolate SLS failures; existing strategy evaluation must continue.
        return None
