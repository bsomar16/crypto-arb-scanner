"""Multi-timeframe Spot Long Signal Engine v9 runner for Binance SPOT candles.

Evaluates only closed candles:
- Primary intervals: 5m, 15m, 1h, 4h, 1d, 1w.
- Correctly aligns prior-closed HTF and BTC benchmark bars.
- Enforces rising-edge trigger (only alerts on new qualification edge).
- Emits SPOT BUY only.
"""
from __future__ import annotations
from threading import RLock
import time
from typing import Dict, List, Optional, Callable, Any

from botutil import http_json, log
from sls_engine import evaluate_series, SetupTracker

INTERVALS = ("5m", "15m", "30m", "45m", "1h", "4h", "1d", "1w")
HTF_MAP = {
    "5m": "1h",
    "15m": "4h",
    "30m": "4h",
    "45m": "4h",
    "1h": "1d",
    "4h": "1w",
    "1d": "1M",
    "1w": None,
}
TF_A_MAP = {
    "5m": "1d",
    "15m": "1d",
    "30m": "1d",
    "45m": "1d",
    "1h": "1d",
    "4h": "1w",
    "1d": "1w",
    "1w": "1M",
}
TF_B_MAP = {
    "5m": "1w",
    "15m": "1w",
    "30m": "1w",
    "45m": "1w",
    "1h": "1w",
    "4h": "1M",
    "1d": "1M",
    "1w": None,
}

_CACHE: Dict[Any, Any] = {}
_LOCK = RLock()
_GLOBAL_SETUP_TRACKER = SetupTracker()


def _parse_closed_candles(raw: List[Any], now_ms: int) -> List[Dict[str, Any]]:
    """Parse Binance klines using their close timestamps, not row position."""
    return [
        {
            "t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]), "v": float(k[5]), "q": float(k[7])
        }
        for k in raw if int(k[6]) < now_ms
    ]


def resample_15m_to_45m(bars_15m: List[Dict[str, Any]], now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    """Resample 15m Binance klines into standard 45m bars aligned to UTC midnight.

    45m bars start at 00:00, 00:45, 01:30, 02:15, 03:00 UTC (32 bars per UTC day).
    Only returns fully closed 45m bars formed by complete 3-bar clusters.
    """
    if not bars_15m:
        return []
    BUCKET_MS = 2_700_000  # 45 minutes in ms
    now = int(time.time() * 1000) if now_ms is None else now_ms
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for b in bars_15m:
        bucket = (int(b["t"]) // BUCKET_MS) * BUCKET_MS
        if bucket + BUCKET_MS <= now:
            groups.setdefault(bucket, []).append(b)

    out = []
    for bucket in sorted(groups.keys()):
        cluster = groups[bucket]
        if len(cluster) < 3:
            continue
        cluster.sort(key=lambda x: int(x["t"]))
        out.append({
            "t": bucket,
            "o": float(cluster[0]["o"]),
            "h": max(float(x["h"]) for x in cluster),
            "l": min(float(x["l"]) for x in cluster),
            "c": float(cluster[-1]["c"]),
            "v": sum(float(x["v"]) for x in cluster),
            "q": sum(float(x.get("q", 0.0)) for x in cluster),
        })
    return out


def _candles(symbol: str, interval: str, limit: int = 240) -> Optional[List[Dict[str, Any]]]:
    key = (symbol, interval, limit)
    now = time.monotonic()
    with _LOCK:
        item = _CACHE.get(key)
        if item and (now - item[0] < 90):
            return item[1]
        # Check if we have a larger or equal slice for (symbol, interval) in cache
        for (c_sym, c_inv, c_lim), (c_ts, c_val) in _CACHE.items():
            if c_sym == symbol and c_inv == interval and (now - c_ts < 90) and c_val:
                if len(c_val) >= limit:
                    sliced = c_val[-limit:]
                    _CACHE[key] = (c_ts, sliced)
                    return sliced

    if interval == "45m":
        # Fetch underlying 15m bars and resample to 45m (limit * 3 bars needed)
        fetch_limit = min(1000, max(300, limit * 3 + 30))
        bars_15m = _candles(symbol, "15m", limit=fetch_limit)
        value = resample_15m_to_45m(bars_15m or [], int(time.time() * 1000))
        if value and len(value) >= 50:
            value = value[-limit:]
        else:
            value = None
        with _LOCK:
            _CACHE[key] = (now, value)
        return value

    # When fetching 15m, fetch at least 750 bars so that 45m resampling reuses it with 0 extra API calls
    effective_limit = max(limit, 750) if interval == "15m" else limit
    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={effective_limit}"
        raw = http_json(url, timeout=12)
        if not raw:
            # Fallback to api.binance.com
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={effective_limit}"
            raw = http_json(url, timeout=12)
        out = _parse_closed_candles(raw, int(time.time() * 1000)) if raw else None
        value = out if (out and len(out) >= 50) else None
    except Exception:
        value = None
    with _LOCK:
        if value is not None and effective_limit != limit:
            _CACHE[(symbol, interval, effective_limit)] = (now, value)
            value = value[-limit:]
        _CACHE[key] = (now, value)
    return value


def _interval_close_ms(open_ms: int, interval: str) -> int:
    if interval == "1M":
        import datetime as dt
        start = dt.datetime.fromtimestamp(open_ms / 1000.0, tz=dt.timezone.utc)
        end = start.replace(year=start.year + 1, month=1, day=1) if start.month == 12 else start.replace(month=start.month + 1, day=1)
        return int(end.timestamp() * 1000)
    units = {
        "1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000, "45m": 2700000, "1h": 3600000,
        "4h": 14400000, "1d": 86400000, "1w": 604800000
    }
    return open_ms + units.get(interval, 3600000)


def _asof_closed(rows: Optional[List[Dict[str, Any]]], primary_close_ms: int,
                 interval: str) -> List[Dict[str, Any]]:
    """Only expose HTF bars that had fully closed by the primary close."""
    return [
        row for row in (rows or [])
        if _interval_close_ms(int(row["t"]), interval) <= primary_close_ms
    ]


def evaluate_sls_coin(coin: str, interval: str = "1h", cfg: Optional[Dict[str, Any]] = None,
                      chg24: float = 0.0,
                      candle_provider: Optional[Callable[..., Optional[List[Dict[str, Any]]]]] = None,
                      error_logger: Optional[Callable[[str], None]] = None) -> Optional[Dict[str, Any]]:
    """Evaluate Spot Long Signal Engine v9 on closed candles."""
    if interval not in INTERVALS:
        return None
    cfg = cfg or {}
    try:
        fetch = candle_provider or _candles
        symbol = str(coin).upper()
        rows = fetch(symbol, interval, 240)
        if not rows or len(rows) < 205:
            return None

        primary_close = _interval_close_ms(int(rows[-1]["t"]), interval)
        htf = HTF_MAP.get(interval)
        htf_rows = None
        if htf:
            raw_htf = fetch(symbol, htf, 240)
            htf_rows = _asof_closed(raw_htf, primary_close, htf)

        # Benchmark context
        benchmark_interval = htf or interval
        raw_btc = fetch("BTC", benchmark_interval, 260)
        btc_rows = _asof_closed(raw_btc, primary_close, benchmark_interval)

        # Higher timeframe support/resistance levels
        tf_a = TF_A_MAP.get(interval)
        tf_b = TF_B_MAP.get(interval)
        tf_a_rows = _asof_closed(fetch(symbol, tf_a, 50), primary_close, tf_a) if tf_a else None
        tf_b_rows = _asof_closed(fetch(symbol, tf_b, 50), primary_close, tf_b) if tf_b else None

        mode = str(cfg.get("sls_sensitivity", "Balanced")).title()
        min_score = cfg.get("sls_min_score")

        result = evaluate_series(
            rows,
            interval=interval,
            htf_rows=htf_rows,
            btc_rows=btc_rows,
            btc_htf_rows=btc_rows,
            sensitivity=mode,
            min_score=min_score,
            setup_tracker=_GLOBAL_SETUP_TRACKER,
            tf_a_rows=tf_a_rows,
            tf_b_rows=tf_b_rows,
        )
        if not result:
            return None

        # Edge detection: check whether the signal was already active on previous closed bar
        if len(rows) >= 2:
            prev_close = _interval_close_ms(int(rows[-2]["t"]), interval)
            prev_htf = _asof_closed(raw_htf, prev_close, htf) if htf else None
            prev_btc = _asof_closed(raw_btc, prev_close, benchmark_interval)
            prev_signal = evaluate_series(
                rows[:-1],
                interval=interval,
                htf_rows=prev_htf,
                btc_rows=prev_btc,
                btc_htf_rows=prev_btc,
                sensitivity=mode,
                min_score=min_score,
                setup_tracker=_GLOBAL_SETUP_TRACKER,
            )
            if prev_signal:
                prev_setup = str(prev_signal.get("setup_type", ""))
                curr_setup = str(result.get("setup_type", ""))
                # Allow upgrade: if prior bar was an EARLY_* setup and this bar confirmed into a primary setup
                is_upgrade = prev_setup.startswith("EARLY_") and not curr_setup.startswith("EARLY_")
                if not is_upgrade:
                    # Rising edge only - suppress persistent qualification
                    return None
                result["setup_upgrade_from"] = prev_setup

        # Enrich result for scanner and notification compatibility
        result["coin"] = symbol
        result["strategy_id"] = f"sls_{interval}"
        result["trade_horizon"] = "Scalp" if interval in ("5m", "15m", "30m") else "Medium" if interval in ("45m", "1h", "4h") else "Long"
        result["chg24"] = float(chg24 or 0.0)
        result["entry_quality"] = result["score"]
        result["expansion_score"] = result["score"]
        result["historical_win_pct"] = None
        result["historical_sample"] = 0

        setup = result.get("setup_type", "SLS")
        reasons = [
            f"SLS v9 score {result['score']:.0f}/100 (req: {result['sls_threshold']:.0f})",
            f"Setup: {setup} · Regime: {result.get('sls_regime', '?')}",
            f"RelVol: ×{result.get('vol_x', 1.0):.2f} · RSI: {result.get('rsi', 0):.0f}",
            "Structure targets TP1-TP5 laddered across overhead resistances",
            "SPOT BUY ONLY · Pine Script v9 engine",
        ]
        if result.get("setup_upgrade_from"):
            reasons.insert(1, f"Confirmed upgrade: {result['setup_upgrade_from']} → {setup}")
        result["reasons"] = reasons
        result["sls_htf"] = htf
        result["sls_btc_confirmed"] = result.get("sls_benchmark") != "B_NA"
        result["trend_4h"] = result.get("sls_regime")
        result["trend_interval"] = htf or interval

        return result

    except Exception as exc:
        if error_logger is not None:
            try:
                error_logger(f"evaluation failed for {coin} {interval}: {exc}")
            except Exception:
                pass
        return None

