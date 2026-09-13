#!/usr/bin/env python3
"""24/7 cross-exchange arbitrage + top movers scanner with Telegram alerts."""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from statistics import median

MIN_EXCHANGES = 4
SPREAD_ALERT_PCT = 1.0
DIGEST_PCT = 0.5

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

BINANCE_FALLBACKS = [
    "https://api.binance.com/api/v3/ticker/price",
    "https://data-api.binance.vision/api/v3/ticker/price",
]

KNOWN_TRAPS = {
    "ELON": "T", "XTER": "T", "RON_L": "T", "DATA": "T", "ARC": "T",
    "FUN": "T", "BEAM": "T", "REEF": "T", "PROS": "T", "HNT": "T",
    "NFP": "T", "BAL": "T", "ACS": "T", "EDGE": "T", "RVV": "T",
    "LIT": "T", "VELO": "T", "UP": "T", "POLS": "T", "ELF": "T",
    "WAVES": "T", "XMR": "T", "ACE": "T", "SNT": "T", "SCRT": "T",
    "PYR": "T", "LRC": "T", "LSK": "T", "PROM": "T", "ICX": "T",
    "STRAX": "T", "ZIL": "T", "MUBARAK": "T", "STEEM": "T", "AGLD": "T",
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
                if t["symbol"].endswith("USDT") and "USDT" not in t["symbol"][:-4]:
                    base = t["symbol"][:-4]
                    if "USDT" in base:
                        continue
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
                if t["currency_pair"].endswith("_USDT"):
                    base = t["currency_pair"][:-5]
                    if "_" in base:
                        continue
                    m[base] = float(t["last"])
        elif name == "MEXC":
            for t in data:
                if t["symbol"].endswith("USDT") and "USDT" not in t["symbol"][:-4]:
                    m[t["symbol"][:-4]] = float(t["price"])
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
        if name == "BINANCE":
            alt = EXCHANGES["BINANCE"]
            if alt.startswith("https://data-api.binance.vision"):
                alt2 = "https://api.binance.com/api/v3/ticker/price"
            else:
                alt2 = "https://data-api.binance.vision/api/v3/ticker/price"
            try:
                data = http_json(alt2, timeout=20)
                m = {}
                for t in data:
                    if t["symbol"].endswith("USDT") and "USDT" not in t["symbol"][:-4]:
                        m[t["symbol"][:-4]] = float(t["price"])
                return m
            except Exception:
                return {}
        return {}


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

    suspects = []
    digest_rows = []
    for c in sorted(universe):
        prices = {ex: p for ex, m in maps.items() if c in m and (p := m[c]) > 0}
        if len(prices) < MIN_EXCHANGES:
            continue
        vals = list(prices.values())
        hi, lo = max(vals), min(vals)
        spread = (hi - lo) / lo * 100
        if c in KNOWN_TRAPS:
            continue
        if spread >= SPREAD_ALERT_PCT:
            lo_ex = min(prices, key=prices.get)
            hi_ex = max(prices, key=prices.get)
            med = median(vals)
            suspects.append({
                "coin": c, "spread": spread,
                "low": lo, "low_ex": lo_ex,
                "high": hi, "high_ex": hi_ex,
                "median": med, "n": len(prices),
            })
        elif spread >= DIGEST_PCT:
            lo_ex = min(prices, key=prices.get)
            hi_ex = max(prices, key=prices.get)
            digest_rows.append({
                "coin": c, "spread": spread, "low_ex": lo_ex,
                "high_ex": hi_ex,
            })
    suspects.sort(key=lambda r: r["spread"], reverse=True)
    digest_rows.sort(key=lambda r: r["spread"], reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"<b>Arb scan {now}</b> | {len(universe)} coins / {len(EXCHANGES)} exchanges"]
    if offline:
        lines.append(f"<b>WARN: offline feeds</b>: {', '.join(offline)} (geo-blocked or down)")
        lines.append("Coverage below excludes these venues.")
    if suspects:
        lines.append("")
        lines.append("<b>ALERTS ({})</b>".format(len(suspects)))
        for r in suspects[:10]:
            lines.append(
                f'<a href="https://www.tradingview.com/symbols/{r["coin"]}USDT/">'
                f'{r["coin"]}</a> +{r["spread"]:.1f}% | '
                f'buy {r["low"]:.6g}@{r["low_ex"]} sell {r["high"]:.6g}@{r["high_ex"]}'
            )
    else:
        lines.append("")
        lines.append("No new candidates above 1%.")
    if digest_rows:
        lines.append("")
        lines.append(f"<b>Watchlist (0.5-1%)</b>")
        for r in digest_rows[:10]:
            lines.append(f'{r["coin"]} +{r["spread"]:.1f}% ({r["low_ex"]}->{r["high_ex"]})')

    text = "\n".join(lines)
    print(text)
    telegram_msg(token, chat_id, text)


if __name__ == "__main__":
    main()