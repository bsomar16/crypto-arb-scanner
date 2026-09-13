#!/usr/bin/env python3
"""24/7 cross-exchange arbitrage + top movers scanner with Telegram alerts."""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from statistics import median

MIN_EXCHANGES = 4
SPREAD_ALERT_PCT = 1.0
TRAP_FILE = "traps.json"

EXCHANGES = {
    "BINANCE": "https://data-api.binance.vision/api/v3/ticker/price",
    "BITGET": "https://api.bitget.com/api/v2/spot/market/tickers",
    "OKX": "https://www.okx.com/api/v5/market/tickers?instType=SPOT",
    "GATE": "https://api.gateio.ws/api/v4/spot/tickers",
    "MEXC": "https://api.mexc.com/api/v3/ticker/price",
    "POLONIEX": "https://api.poloniex.com/markets/ticker24h",
    "KUCOIN": "https://api.kucoin.com/api/v1/market/allTickers",
    "HTX": "https://api.huobi.pro/market/tickers",
    "COINEX": "https://api.coinex.com/v2/spot/ticker",
}


def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_exchange(name):
    try:
        data = http_json(EXCHANGES[name])
        m = {}
        if name == "BINANCE":
            for t in data:
                s = t["symbol"]
                if s.endswith("USDT") and s != "USDTUSDT":
                    base = s[:-4]
                    if "USDT" not in base:
                        m[base] = float(t["price"])
        elif name == "BITGET":
            for t in data.get("data", []):
                if t["symbol"].endswith("USDT"):
                    m[t["symbol"][:-4]] = float(t["lastPr"])
        elif name == "OKX":
            for t in data.get("data", []):
                if t["instId"].endswith("-USDT"):
                    m[t["instId"][:-5]] = float(t["last"])
        elif name == "GATE":
            for t in data:
                cp = t["currency_pair"]
                if cp.endswith("_USDT") and "_" not in cp[:-5]:
                    m[cp[:-5]] = float(t["last"])
        elif name == "MEXC":
            for t in data:
                s = t["symbol"]
                if s.endswith("USDT") and s != "USDTUSDT":
                    base = s[:-4]
                    if "USDT" not in base:
                        m[base] = float(t["price"])
        elif name == "POLONIEX":
            for t in data:
                if t["symbol"].endswith("_USDT"):
                    m[t["symbol"][:-5]] = float(t["close"])
        elif name == "KUCOIN":
            for t in data["data"]["ticker"]:
                if t["symbol"].endswith("-USDT"):
                    m[t["symbol"][:-5]] = float(t["last"])
        elif name == "HTX":
            for t in data.get("data", []):
                if t["symbol"].endswith("usdt"):
                    m[t["symbol"][:-4].upper()] = float(t["close"])
        elif name == "COINEX":
            for t in data.get("data", []):
                if t["market"].endswith("USDT"):
                    m[t["market"][:-4]] = float(t["last"])
        return m
    except Exception:
        return {}


def fetch_binance_24h():
    """Top movers from Binance 24hr ticker (vision mirror)."""
    try:
        data = http_json("https://data-api.binance.vision/api/v3/ticker/24hr",
                         timeout=25)
        rows = []
        for t in data:
            s = t["symbol"]
            if not (s.endswith("USDT") and s != "USDTUSDT"):
                continue
            base = s[:-4]
            if "USDT" in base or "FDUSD" in base or "BUSD" in base:
                continue
            try:
                q = float(t["quoteVolume"])
                ch = float(t["priceChangePercent"])
            except (ValueError, KeyError):
                continue
            if q < 500000:
                continue
            rows.append((base, ch, q))
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows
    except Exception:
        return []


def telegram_msg(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        sys.exit(1)

    traps = set()
    if os.path.exists(TRAP_FILE):
        try:
            traps = set(json.load(open(TRAP_FILE)))
        except Exception:
            traps = set()

    maps = {}
    offline = []
    for ex in EXCHANGES:
        maps[ex] = fetch_exchange(ex)
        print(f"{ex}: {len(maps[ex])} pairs")
        if not maps[ex]:
            offline.append(ex)

    counts = {}
    for ex, m in maps.items():
        for c in m:
            counts[c] = counts.get(c, 0) + 1
    universe = [c for c, n in counts.items() if n >= MIN_EXCHANGES]

    new_alerts = []
    watch = []
    all_flagged = set()
    for c in sorted(universe):
        prices = {ex: p for ex, m in maps.items() if c in m and (p := m[c]) > 0}
        if len(prices) < MIN_EXCHANGES:
            continue
        vals = list(prices.values())
        hi, lo = max(vals), min(vals)
        spread = (hi - lo) / lo * 100
        if spread >= SPREAD_ALERT_PCT:
            all_flagged.add(c)
            if c in traps:
                continue
            lo_ex = min(prices, key=prices.get)
            hi_ex = max(prices, key=prices.get)
            med = median(vals)
            new_alerts.append({"coin": c, "spread": spread, "low": lo,
                               "low_ex": lo_ex, "high": hi,
                               "high_ex": hi_ex, "median": med})
        elif spread >= 0.5 and c not in traps:
            lo_ex = min(prices, key=prices.get)
            hi_ex = max(prices, key=prices.get)
            watch.append({"coin": c, "spread": spread, "low_ex": lo_ex,
                          "high_ex": hi_ex})
    new_alerts.sort(key=lambda r: r["spread"], reverse=True)
    watch.sort(key=lambda r: r["spread"], reverse=True)

    # persist new flagged coins so they never re-alert
    traps |= all_flagged
    with open(TRAP_FILE, "w") as f:
        json.dump(sorted(traps), f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"<b>Arb scan {now}</b> | {len(universe)} coins / {len(EXCHANGES)} exchanges"]
    if offline:
        lines.append(f"<b>WARN offline:</b> {', '.join(offline)}")
    if new_alerts:
        lines.append("")
        lines.append(f"<b>NEW ALERTS ({len(new_alerts)})</b>")
        for r in new_alerts[:10]:
            lines.append(
                f'<a href="https://www.tradingview.com/symbols/{r["coin"]}USDT/">'
                f'{r["coin"]}</a> +{r["spread"]:.1f}% | '
                f'buy {r["low"]:.5g}@{r["low_ex"]} sell {r["high"]:.5g}@{r["high_ex"]}'
            )
    else:
        lines.append("")
        lines.append("No new candidates above 1%.")
    if watch:
        lines.append("")
        lines.append("<b>Watchlist (0.5-1%)</b>")
        for r in watch[:8]:
            lines.append(f'{r["coin"]} +{r["spread"]:.1f}% ({r["low_ex"]}->{r["high_ex"]})')

    movers = fetch_binance_24h()
    if movers:
        lines.append("")
        lines.append("<b>Top movers (24h, vol>500k USDT)</b>")
        g = movers[:4]
        l = [r for r in movers if r[1] < 0][-4:]
        l = l[::-1]
        lines.append("G:" + " ".join(f'{r[0]} {r[1]:+.1f}%' for r in g))
        lines.append("L:" + " ".join(f'{r[0]} {r[1]:+.1f}%' for r in l))

    text = "\n".join(lines)
    print(text)
    telegram_msg(token, chat_id, text)


if __name__ == "__main__":
    main()