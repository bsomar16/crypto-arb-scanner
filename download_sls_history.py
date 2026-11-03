#!/usr/bin/env python3
"""Download resumable Binance Spot candles for SLS historical replay.

Example:
  python download_sls_history.py --start 2024-01-01 --end 2025-01-01 \
      --symbols BTC ETH SOL --output data/sls_candles.json

The end date is exclusive. The output uses the JSON schema consumed by
sls_backtest.py and stores Binance open times in UTC milliseconds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://data-api.binance.vision"
INTERVALS = ("5m", "15m", "1h", "4h", "1d", "1w", "1M")
LIMIT = 1000


def _utc_ms(value: str) -> int:
    """Parse YYYY-MM-DD or ISO-8601 and return UTC epoch milliseconds."""
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.astimezone(dt.timezone.utc).timestamp() * 1000)


def _http_json(url: str, timeout: int = 20):
    request = Request(url, headers={"User-Agent": "crypto-arb-scanner-sls-research/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _spot_symbols(fetch_json=_http_json):
    payload = fetch_json(f"{BASE_URL}/api/v3/exchangeInfo")
    return {
        item["symbol"]
        for item in payload.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and item.get("isSpotTradingAllowed", False)
    }


def _parse_kline(row):
    return {
        "t": int(row[0]), "o": float(row[1]), "h": float(row[2]),
        "l": float(row[3]), "c": float(row[4]), "v": float(row[5]),
        "q": float(row[7]),
    }


def _download_pair(symbol, interval, start_ms, end_ms, fetch_json=_http_json, pause=0.05):
    """Fetch [start_ms, end_ms) with Binance's 1000-row pagination."""
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        query = urlencode({
            "symbol": symbol, "interval": interval, "startTime": cursor,
            "endTime": end_ms - 1, "limit": LIMIT,
        })
        payload = fetch_json(f"{BASE_URL}/api/v3/klines?{query}")
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected Binance kline response for {symbol} {interval}: {payload}")
        if not payload:
            break
        batch = [_parse_kline(row) for row in payload if start_ms <= int(row[0]) < end_ms]
        rows.extend(batch)
        last_open = int(payload[-1][0])
        next_cursor = last_open + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"Binance pagination did not advance for {symbol} {interval}")
        cursor = next_cursor
        if len(payload) < LIMIT:
            break
        if pause:
            time.sleep(pause)
    unique = {row["t"]: row for row in rows}
    return [unique[timestamp] for timestamp in sorted(unique)]


def download_dataset(symbols, intervals, start_ms, end_ms, output,
                     fetch_json=_http_json, pause=0.05):
    if start_ms >= end_ms:
        raise ValueError("start must be earlier than end")
    valid_symbols = _spot_symbols(fetch_json)
    normalised = []
    for value in symbols:
        symbol = value.upper()
        pair = symbol if symbol.endswith("USDT") else symbol + "USDT"
        if pair not in valid_symbols:
            raise ValueError(f"{pair} is not an active Binance USDT Spot symbol")
        if pair not in normalised:
            normalised.append(pair)
    if "BTCUSDT" not in normalised:
        if "BTCUSDT" not in valid_symbols:
            raise ValueError("BTCUSDT benchmark is not available on Binance Spot")
        normalised.insert(0, "BTCUSDT")

    path = Path(output)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"candles": {}}
    candles = payload.setdefault("candles", {})
    for pair in normalised:
        coin = pair[:-4]
        by_interval = candles.setdefault(coin, {})
        for interval in intervals:
            if interval not in INTERVALS:
                raise ValueError(f"Unsupported interval: {interval}")
            existing = by_interval.setdefault(interval, [])
            existing_by_time = {int(row["t"]): row for row in existing}
            # Resume from the last saved open time; overlapping one candle is
            # harmless and protects against an interrupted final write.
            cursor = max(start_ms, max(existing_by_time, default=start_ms - 1))
            fetch_start = max(start_ms, cursor)
            fetched = _download_pair(pair, interval, fetch_start, end_ms, fetch_json, pause)
            for row in fetched:
                existing_by_time[row["t"]] = row
            by_interval[interval] = [existing_by_time[t] for t in sorted(existing_by_time)
                                     if start_ms <= t < end_ms]
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps({"candles": candles}, separators=(",", ":")), encoding="utf-8")
            temp.replace(path)
    return {"symbols": [pair[:-4] for pair in normalised],
            "intervals": list(intervals), "output": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC date/time; end is exclusive")
    parser.add_argument("--end", required=True, help="UTC date/time; end is exclusive")
    parser.add_argument("--symbols", nargs="+", help="base assets, e.g. BTC ETH SOL; default config backtest_symbols")
    parser.add_argument("--intervals", nargs="+", default=list(INTERVALS),
                        choices=list(INTERVALS), help="include HTF intervals (notably 1M)")
    parser.add_argument("--output", default="data/sls_candles.json")
    parser.add_argument("--pause", type=float, default=0.05, help="seconds between full pages")
    args = parser.parse_args()
    symbols = args.symbols
    if not symbols:
        config = json.loads(Path("config.json").read_text(encoding="utf-8"))
        symbols = config.get("backtest_symbols") or config.get("watchlist", ["BTC"])
    result = download_dataset(symbols, args.intervals, _utc_ms(args.start),
                              _utc_ms(args.end), args.output, pause=max(0, args.pause))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
