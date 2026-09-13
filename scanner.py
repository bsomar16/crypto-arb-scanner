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
    """Top movers + quote volume from Binance 24hr ticker (vision mirror)."""
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
            if q < 1000000:
                continue
            rows.append([base, ch, q])
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows
    except Exception:
        return []


def volume_spike(symbol, hours=25):
    """Ratio of last 1h volume vs avg of previous 24h (Binance klines)."""
    try:
        data = http_json(
            f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}USDT"
            f"&interval=1h&limit={hours}", timeout=20)
        vols = [float(k[5]) for k in data]
        if len(vols) < 12:
            return None
        last = vols[-1]
        avg = sum(vols[:-1]) / len(vols[:-1])
        if avg <= 0:
            return None
        return last / avg
    except Exception:
        return None


def chain_summary(chans):
    """Chans: list of (name, dep_ok, wd_ok). Return open nets + a flag."""
    open_n = [n for n, d, w in chans if d and w]
    if open_n:
        cap = open_n[:5]
        more = f" +{len(open_n) - 5}" if len(open_n) > 5 else ""
        return " ".join(cap) + more, True
    partial = [f"{n}(" + ("D" if d else "-") + ("W" if w else "-") + ")"
               for n, d, w in chans if d or w]
    return (" ".join(partial[:4])) if partial else "all closed", False


def fetch_coin_status(coin):
    """Collect deposit/withdraw + network info across 4 exchanges."""
    out = {}
    # KuCoin
    try:
        d = http_json(f"https://api.kucoin.com/api/v1/currencies/{coin}",
                      timeout=15)
        dd = d["data"]
        dep = bool(dd.get("isDepositEnabled"))
        wd = bool(dd.get("isWithdrawEnabled"))
        fee = dd.get("withdrawalMinFee")
        note = f"min fee {fee}" if fee not in (None, "", "0") else ""
        out["KUCOIN"] = {"dep": dep, "wd": wd, "net": ["chain"], "note": note}
    except Exception:
        pass
    # Gate
    try:
        d = http_json(f"https://api.gateio.ws/api/v4/spot/currencies/{coin}",
                      timeout=15)
        chans = [(c.get("name", "?"), not c.get("deposit_disabled"),
                  not c.get("withdraw_disabled")) for c in d.get("chains", [])]
        if chans:
            net, fully = chain_summary(chans)
            out["GATE"] = {"dep": fully or any(c[1] for c in chans),
                           "wd": fully or any(c[2] for c in chans),
                           "net": [net], "note": ""}
    except Exception:
        pass
    # HTX
    try:
        d = http_json("https://api.huobi.pro/v2/reference/currencies",
                      timeout=25)
        for cur in d.get("data", []):
            if cur.get("currency", "").lower() == coin.lower():
                chans = [(c.get("displayName", "?"),
                          c.get("depositStatus") == "allowed",
                          c.get("withdrawStatus") == "allowed")
                         for c in cur.get("chains", [])]
                if chans:
                    net, fully = chain_summary(chans)
                    out["HTX"] = {"dep": fully or any(c[1] for c in chans),
                                  "wd": fully or any(c[2] for c in chans),
                                  "net": [net], "note": ""}
                break
    except Exception:
        pass
    # Bitget
    try:
        d = http_json("https://api.bitget.com/api/v2/spot/public/coins",
                      timeout=25)
        for cur in d.get("data", []):
            if cur.get("coin", "").upper() == coin.upper():
                chans = [(c.get("chain", "?"),
                          str(c.get("rechargeable", "false")).lower() == "true",
                          str(c.get("withdrawable", "false")).lower() == "true")
                         for c in cur.get("chains", [])]
                if chans:
                    net, fully = chain_summary(chans)
                    out["BITGET"] = {"dep": fully or any(c[1] for c in chans),
                                     "wd": fully or any(c[2] for c in chans),
                                     "net": [net], "note": ""}
                break
    except Exception:
        pass
    return out


STATUS_ICON = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "bad": "\u274c", "unknown": "\u2753"}


def fmt_status(ex, info):
    dep, wd = info["dep"], info["wd"]
    icon = STATUS_ICON["ok"] if (dep and wd) else (STATUS_ICON["warn"] if (dep or wd) else STATUS_ICON["bad"])
    note = f" | {info['note']}" if info.get("note") else ""
    net = info["net"][0]
    nmark = f"nets: {net}" if not (dep and wd) else f"nets: {net}"
    return f"   {icon} <b>{ex}</b>  D:{'on' if dep else 'off'} W:{'on' if wd else 'off'}{note}\n      {nmark}"


def telegram_msg(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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

    traps |= all_flagged
    with open(TRAP_FILE, "w") as f:
        json.dump(sorted(traps), f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bar = "──────" * 3

    lines = []
    lines.append(f"\U0001f4ca <b>ARB SCAN</b> \u00b7 {now}")
    lines.append(f"\U0001f310 {len(universe)} coins \u00b7 {len(EXCHANGES)} exchanges"
                 f" \u00b7 {len(EXCHANGES) - len(offline)}/9 feeds live")

    if new_alerts:
        lines.append("")
        lines.append(f"\U0001f6a8 <b>NEW ALERTS ({len(new_alerts)})</b>")
        for r in new_alerts[:5]:
            lines.append(f"\u2588 {esc(r['coin'])}  <b>+{r['spread']:.1f}%</b> (median {r['median']:.5g})")
            lines.append(f"   \U0001f4e4 buy  {r['low']:.6g} \u00b7 <b>{r['low_ex']}</b>")
            lines.append(f"   \U0001f4e5 sell {r['high']:.6g} \u00b7 <b>{r['high_ex']}</b>")
            st = fetch_coin_status(r["coin"])
            if st:
                lines.append(f"   \U0001f6f0\ufe0f <b>deposit / withdraw</b>")
                for ex in ("KUCOIN", "GATE", "HTX", "BITGET"):
                    if ex in st:
                        lines.append("   " + fmt_status(ex, st[ex]))
        lines.append(bar)

    if watch:
        lines.append("")
        lines.append(f"\U0001f440 <b>WATCHLIST (0.5\u20131%)</b>")
        for r in watch[:6]:
            lines.append(f"   {r['coin']}  +{r['spread']:.1f}%  ({r['low_ex']}\u2192{r['high_ex']})")
        lines.append(bar)

    movers = fetch_binance_24h()
    if movers:
        gainers = movers[:5]
        losers = sorted([r for r in movers if r[1] < 0], key=lambda r: r[1])[:5]
        lines.append("")
        lines.append(f"\U0001f4c8 <b>TOP GAINERS 24h</b> (BN)")
        for i, (b, ch, q) in enumerate(gainers, 1):
            spike = volume_spike(b)
            v = f" \u00b7 <b>vol x{spike:.1f}</b>" if spike else ""
            lines.append(f"  {i}. {b}  +{ch:.1f}%{v}")
        lines.append("")
        lines.append(f"\U0001f4c9 <b>TOP LOSERS 24h</b> (BN)")
        for i, (b, ch, q) in enumerate(losers, 1):
            spike = volume_spike(b)
            v = f" \u00b7 <b>vol x{spike:.1f}</b>" if spike else ""
            lines.append(f"  {i}. {b}  {ch:.1f}%{v}")

    if offline:
        lines.append("")
        lines.append(f"\u26a0\ufe0f offline feeds: {', '.join(offline)}")

    text = "\n".join(lines)
    print(text)
    if len(text) > 4000:
        text = text[:4000] + "..."
    telegram_msg(token, chat_id, text)


if __name__ == "__main__":
    main()