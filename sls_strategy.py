"""Native, long-only TradingView SLS port for Binance SPOT candles."""
from threading import RLock
import time
from sls_pine_math import pine_atr, pine_ema, pine_macd, pine_rsi
from sls_engine import evaluate_series
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
    """Return the latest Pine ta.atr-compatible Wilder ATR value."""
    values = pine_atr(highs, lows, closes, period)
    return values[-1] if values else None

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
    """Pine RSI series; warm-up bars remain None rather than neutral 50."""
    return pine_rsi(values, period)


def _state(rows, min_history=200):
    """Build indicator series with Pine-compatible initialization/warm-up."""
    if not rows or len(rows) < min_history:
        return None
    close = [x["c"] for x in rows]
    e20, e50 = pine_ema(close, 20), pine_ema(close, 50)
    # Do not expose EMA200 as a usable HTF feature before 200 closed candles.
    e200 = pine_ema(close, 200) if len(close) >= 200 else [None] * len(close)
    macd, sig, hist = pine_macd(close, 12, 26, 9)
    rsi = pine_rsi(close, 14)
    if any(x is None for x in (e20[-1], e50[-1], macd[-1], sig[-1], hist[-1], rsi[-1])):
        return None
    return {"close": close[-1], "closes": close, "e20": e20, "e50": e50,
            "e200": e200 if len(close) >= 200 else None,
            "macd": macd, "sig": sig, "hist": hist, "rsi": rsi[-1], "rsi_series": rsi}


def _interval_close_ms(open_ms, interval):
    if interval == "1M":
        import datetime as dt
        start = dt.datetime.fromtimestamp(open_ms / 1000, tz=dt.timezone.utc)
        end = start.replace(year=start.year + 1, month=1, day=1) if start.month == 12 else start.replace(month=start.month + 1, day=1)
        return int(end.timestamp() * 1000)
    units = {"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000,"1w":604800000}
    return open_ms + units[interval]


def _asof_closed(rows, primary_close_ms, interval):
    """Only expose HTF bars that had fully closed by the primary close."""
    return [row for row in (rows or [])
            if _interval_close_ms(int(row["t"]), interval) <= primary_close_ms]


def evaluate_sls_coin(coin, interval="1h", cfg=None, chg24=0.0, candle_provider=None, error_logger=None):
    """Evaluate SLS on closed Binance Spot candles; emits SLS-attributed BUY only."""
    if interval not in INTERVALS:
        return None
    cfg = cfg or {}
    try:
        fetch = candle_provider or _candles
        symbol = str(coin).upper()
        rows = fetch(symbol, interval)
        if not rows or len(rows) < 205:
            return None
        primary_close = _interval_close_ms(int(rows[-1]["t"]), interval)
        htf = HTF_MAP[interval]
        htf_rows = None
        if htf:
            raw_htf = fetch(symbol, htf, 240)
            htf_rows = _asof_closed(raw_htf, primary_close, htf)
        # Pine benchmark is evaluated on the same HTF context as the symbol.
        benchmark_interval = htf or interval
        raw_btc = fetch("BTC", benchmark_interval, 260)
        btc_rows = _asof_closed(raw_btc, primary_close, benchmark_interval)
        mode = str(cfg.get("sls_sensitivity", "Balanced")).title()
        result = evaluate_series(
            rows, interval, htf_rows=htf_rows, btc_rows=btc_rows,
            sensitivity=mode, min_score=cfg.get("sls_min_score"),
        )
        if not result:
            return None
        # Pine buyEvent is a rising edge, not a persistent qualifying state.
        # Compare against the prior closed bar with historically available
        # HTF and BTC context only.
        if len(rows) >= 2:
            prev_close = _interval_close_ms(int(rows[-2]["t"]), interval)
            prev_htf = _asof_closed(raw_htf, prev_close, htf) if htf else None
            prev_btc = _asof_closed(raw_btc, prev_close, benchmark_interval)
            prev_signal = evaluate_series(
                rows[:-1], interval, htf_rows=prev_htf, btc_rows=prev_btc,
                sensitivity=mode, min_score=cfg.get("sls_min_score"))
            if prev_signal:
                return None
        result["coin"] = symbol
        result["strategy_id"] = f"sls_{interval}"
        result["trade_horizon"] = "Scalp" if interval in ("5m","15m") else "Medium" if interval in ("1h","4h") else "Long"
        result["setup_type"] = "BREAKOUT" if result.get("sls_components",{}).get("c5",0)>=4 else "TREND_PULLBACK"
        result["potential_pct"] = round((result["t3"]/result["entry"]-1)*100,2)
        result["chg24"] = float(chg24 or 0)
        result["entry_quality"] = result["score"]
        result["expansion_score"] = result["score"]
        result["reasons"] = [
            f"SLS score {result['score']:.1f}/100 (threshold {result['sls_threshold']:.0f})",
            f"Regime {result.get('sls_regime')}; BTC {result.get('sls_benchmark')}",
            "Closed-candle Pine-rule evaluation; SPOT BUY only",
            "ATR stop 1.5x; targets 1.5R/2.5R/4R/6R/8R",
        ]
        result["sls_htf"] = htf
        result["sls_btc_confirmed"] = result.get("sls_benchmark") != "B_NA"
        result["trend_4h"] = result.get("sls_regime")
        result["trend_interval"] = htf or interval
        result["mtf_1h"] = result["mtf_1d"] = result["mtf_1w"] = "N/A"
        result["estimated_hold_min_hours"] = result["estimated_hold_max_hours"] = None
        return result
    except Exception as exc:
        # Isolate SLS failures; make evaluation faults observable without
        # allowing a single symbol/timeframe to stop the broader scan.
        if error_logger is not None:
            try:
                detail = str(exc).replace("\\n", " ")[:240]
                error_logger(f"evaluation failed for {str(coin).upper()} {interval}: {type(exc).__name__}: {detail}")
            except Exception:
                pass
        return None
