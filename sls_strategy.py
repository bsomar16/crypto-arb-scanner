"""Native, long-only TradingView SLS port for Binance SPOT candles."""
from threading import RLock
import time
import indicators as ind
from botutil import http_json

INTERVALS = ("5m", "15m", "1h", "4h", "1d", "1w")
HTF_MAP = {"5m": "1h", "15m": "4h", "1h": "1d", "4h": "1w", "1d": "1M", "1w": None}
THRESHOLDS = {"Conservative": 90, "Balanced": 86, "Aggressive": 82}
_CACHE, _LOCK = {}, RLock()


def _parse_closed_candles(raw, now_ms):
    """Parse Binance klines using their close timestamps, not row position."""
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
         "c": float(k[4]), "v": float(k[5]), "q": float(k[7])}
        for k in raw if int(k[6]) < now_ms
    ]


def _wilder_atr(highs, lows, closes, period=14):
    """Pine ta.atr-style Wilder RMA of true range, seeded by an SMA."""
    if period <= 0 or len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    if len(true_ranges) < period:
        return None
    value = sum(true_ranges[:period]) / period
    for current in true_ranges[period:]:
        value = (value * (period - 1) + current) / period
    return value

def _candles(symbol, interval, limit=240):
    key = (symbol, interval, limit)
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] < 45:
            return item[1]
    try:
        raw = http_json(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}", timeout=12)
        # Use exchange close timestamps: blindly dropping the final row can
        # discard a fully closed candle when the API response ends at a boundary.
        out = _parse_closed_candles(raw, int(time.time() * 1000))
        value = out if len(out) >= 50 else None
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
    """Wilder RSI aligned to candle indices, matching indicators.rsi()."""
    out = [50.0] * len(values)
    if len(values) <= period:
        return out
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    avg_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def rsi_value(gain, loss):
        # Keep parity with indicators.rsi(), which returns 100 when loss is zero.
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = rsi_value(avg_gain, avg_loss)
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = rsi_value(avg_gain, avg_loss)
    return out


def _state(rows, min_history=200):
    """Build trend state; the primary chart requires EMA200 history.

    HTF/BTC confirmation only consumes EMA20/EMA50, so those contexts need
    50 bars rather than an unnecessary 200-bar EMA200 history requirement.
    """
    if not rows or len(rows) < min_history:
        return None
    close = [x["c"] for x in rows]
    e20, e50 = ind.ema(close, 20), ind.ema(close, 50)
    e200 = ind.ema(close, 200) if len(close) >= 200 else None
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
        atr = float(_wilder_atr(high, low, close) or 0)
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
            hs = _state(fetch(str(coin).upper(), htf, 220), min_history=50)
            if hs:
                htf_ok = hs["close"] > hs["e20"][-1] > hs["e50"][-1]
                htf_state = "BULLISH" if htf_ok else "NOT_BULLISH"
        bs = _state(fetch("BTC", interval, 220), min_history=50)
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
        t1, t2, t3, t4, t5 = ladder
        potential = (t3 / price - 1) * 100
        return {
            "coin": str(coin).upper(), "interval": interval, "strategy": "SLS",
            "signal_source": "SLS", "signal_engine": "Spot Long Signal Engine",
            "strategy_id": f"sls_{interval}", "strategy_family": "SLS",
            "trade_horizon": "Scalp" if interval in ("5m", "15m") else "Medium" if interval in ("1h", "4h") else "Long",
            "setup_type": "BREAKOUT" if breakout else "TREND_PULLBACK",
            "signal_action": "BUY", "candle_open_time": rows[-1]["t"],
            "price": price, "entry": price, "stop": stop,
            "t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5, "target": t3,
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
