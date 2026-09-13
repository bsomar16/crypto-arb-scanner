#!/usr/bin/env python3
"""Crypto signal bot: daily Top-20 buy picks + 15-min arb alerts via Telegram."""

import json
import os
import sys
import urllib.request
import concurrent.futures
from argparse import ArgumentParser
from datetime import datetime, timezone
from statistics import mean, median

MIN_EXCHANGES = 4
SPREAD_ALERT_PCT = 1.0
TRAP_FILE = "traps.json"
TOP_N = 20
MIN_QV = 1500000  # min 24h quote volume for daily picks

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


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def telegram_msg(token, chat_id, text):
    if len(text) > 4000:
        text = text[:3990] + "\n…(truncated)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


# ────────────────────────── ARB SCANNER ──────────────────────────

def fetch_exchange(name):
    try:
        data = http_json(EXCHANGES[name])
        m = {}
        if name == "BINANCE":
            for t in data:
                s = t["symbol"]
                if s.endswith("USDT") and s != "USDTUSDT":
                    b = s[:-4]
                    if "USDT" not in b:
                        m[b] = float(t["price"])
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
                    b = s[:-4]
                    if "USDT" not in b:
                        m[b] = float(t["price"])
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
    try:
        return http_json("https://data-api.binance.vision/api/v3/ticker/24hr",
                         timeout=25)
    except Exception:
        return []


def volume_spike(symbol, hours=25):
    try:
        data = http_json(
            f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}USDT"
            f"&interval=1h&limit={hours}", timeout=20)
        vols = [float(k[5]) for k in data]
        if len(vols) < 12:
            return None
        last = mean(vols[-2:])
        avg = mean(vols[:-2])
        return last / avg if avg > 0 else None
    except Exception:
        return None


def chain_summary(chans):
    open_n = [n for n, d, w in chans if d and w]
    if open_n:
        cap = open_n[:5]
        more = f" +{len(open_n) - 5}" if len(open_n) > 5 else ""
        return " ".join(cap) + more, True
    partial = [f"{n}(" + ("D" if d else "-") + ("W" if w else "-") + ")"
               for n, d, w in chans if d or w]
    return " ".join(partial[:4]) if partial else "all closed", False


def fetch_coin_status(coin):
    out = {}
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


STATUS_ICON = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "bad": "\u274c"}


def fmt_status(ex, info):
    dep, wd = info["dep"], info["wd"]
    icon = STATUS_ICON["ok"] if (dep and wd) else (STATUS_ICON["warn"] if (dep or wd) else STATUS_ICON["bad"])
    note = f" | {info['note']}" if info.get("note") else ""
    return (f"   {icon} <b>{ex}</b>  D:{'on' if dep else 'off'}"
            f" W:{'on' if wd else 'off'}{note}\n      nets: {info['net'][0]}")


def run_arb(token, chat_id):
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

    new_alerts, watch, all_flagged = [], [], set()
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
            new_alerts.append({"coin": c, "spread": spread, "low": lo,
                               "low_ex": min(prices, key=prices.get),
                               "high": hi, "high_ex": max(prices, key=prices.get),
                               "median": median(vals)})
        elif spread >= 0.5 and c not in traps:
            watch.append({"coin": c, "spread": spread,
                          "low_ex": min(prices, key=prices.get),
                          "high_ex": max(prices, key=prices.get)})
    new_alerts.sort(key=lambda r: r["spread"], reverse=True)
    watch.sort(key=lambda r: r["spread"], reverse=True)

    traps |= all_flagged
    with open(TRAP_FILE, "w") as f:
        json.dump(sorted(traps), f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bar = "\u2501" * 18
    lines = [f"\U0001f4ca <b>ARB SCAN</b> \u00b7 {now}",
             f"\U0001f310 {len(universe)} coins \u00b7 {len(EXCHANGES)} exchanges"
             f" \u00b7 {len(EXCHANGES) - len(offline)}/9 feeds"]

    if new_alerts:
        lines.append("")
        lines.append(f"\U0001f6a8 <b>NEW ALERTS ({len(new_alerts)})</b>")
        for r in new_alerts[:5]:
            lines.append(f"\n\U0001f525 {esc(r['coin'])}  <b>+{r['spread']:.1f}%</b>")
            lines.append(f"   \U0001f4e4 buy  {r['low']:.6g} \u00b7 <b>{r['low_ex']}</b>")
            lines.append(f"   \U0001f4e5 sell {r['high']:.6g} \u00b7 <b>{r['high_ex']}</b>")
            st = fetch_coin_status(r["coin"])
            if st:
                lines.append(f"   \U0001f6f0\ufe0f <b>deposit / withdraw</b>")
                for ex in ("KUCOIN", "GATE", "HTX", "BITGET"):
                    if ex in st:
                        lines.append(fmt_status(ex, st[ex]))
        lines.append(bar)
    else:
        lines.append("")
        lines.append("No new cross-exchange gaps. \u2705")

    if watch:
        lines.append("")
        lines.append(f"\U0001f440 <b>WATCHLIST (0.5\u20131%)</b>")
        for r in watch[:5]:
            lines.append(f"   {r['coin']}  +{r['spread']:.1f}%  ({r['low_ex']}\u2192{r['high_ex']})")

    movers = fetch_binance_24h()
    if movers:
        gainers = []
        for t in movers:
            s = t["symbol"]
            if not (s.endswith("USDT") and s != "USDTUSDT"):
                continue
            b = s[:-4]
            if "USDT" in b or "FDUSD" in b or "BUSD" in b:
                continue
            try:
                q = float(t["quoteVolume"])
                ch = float(t["priceChangePercent"])
                if q >= 500000 and ch > 0:
                    gainers.append([b, ch, q])
            except (ValueError, KeyError):
                continue
        gainers.sort(key=lambda r: r[1], reverse=True)
        if gainers:
            lines.append("")
            lines.append(f"\U0001f4c8 <b>TOP GAINERS 24h</b> (BN)")
            for i, (b, ch, q) in enumerate(gainers[:5], 1):
                spike = volume_spike(b)
                v = f" \u00b7 <b>vol x{spike:.1f}</b>" if spike else ""
                lines.append(f"   {i}. {b}  +{ch:.1f}%{v}")

    if offline:
        lines.append("")
        lines.append(f"\u26a0\ufe0f offline feeds: {', '.join(offline)}")

    text = "\n".join(lines)
    print(f"[ARB] {len(new_alerts)} alerts, {len(watch)} watch, {len(gainers) if 'gainers' in dir() else 0} gainers")
    telegram_msg(token, chat_id, text)
    return True


# ────────────────────────── DAILY TOP 20 ──────────────────────────

def ema(vals, period):
    k = 2 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = mean(gains[:period]), mean(losses[:period])
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100
    return 100 - 100 / (1 + ag / al)


def indicators(closes):
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    return {"rsi": rsi(closes), "macd": macd[-1], "macd_sig": sig[-1],
            "e20": e20[-1], "e50": e50[-1], "close": closes[-1]}


def score(ind, vol_ratio, chg24):
    s = 0
    if ind["close"] > ind["e20"]:
        s += 15
    if ind["e20"] > ind["e50"]:
        s += 15
    r = ind["rsi"] or 50
    if 45 <= r <= 68:
        s += 15
    elif 35 <= r < 45 or 68 < r <= 75:
        s += 8
    elif 25 <= r < 35 or 75 < r <= 80:
        s += 3
    if r > 82:
        s -= 15
    if r < 28:
        s -= 10
    if ind["macd"] > ind["macd_sig"]:
        s += 12
    if ind["macd"] > 0:
        s += 8
    if vol_ratio >= 3:
        s += 15
    elif vol_ratio >= 2:
        s += 12
    elif vol_ratio >= 1.5:
        s += 9
    elif vol_ratio >= 1.0:
        s += 5
    elif vol_ratio >= 0.7:
        s += 2
    if 0 <= chg24 <= 10:
        s += 10
    elif 10 < chg24 <= 20:
        s += 6
    elif -5 <= chg24 < 0:
        s += 4
    elif chg24 > 30:
        s -= 6
    if ind["macd"] > 0 and ind["macd"] > ind["macd_sig"] and ind["close"] > ind["e20"]:
        s += 5
    return max(0, min(100, s))


def rating(s):
    if s >= 75:
        return "STRONG BUY"
    if s >= 60:
        return "BUY"
    if s >= 50:
        return "WATCH"
    return "AVOID"


def analyze_coin(coin):
    try:
        data = http_json(
            f"https://data-api.binance.vision/api/v3/klines?symbol={coin}USDT"
            f"&interval=1d&limit=70", timeout=15)
        if len(data) < 35:
            return None
        closes = [float(k[4]) for k in data][:-1]
        ind = indicators(closes)
        t24 = http_json(
            f"https://data-api.binance.vision/api/v3/ticker/24hr?symbol={coin}USDT",
            timeout=15)
        chg24 = float(t24["priceChangePercent"])
        qv = float(t24["quoteVolume"])
        vol_x = volume_spike(coin)
        s = score(ind, vol_x or 0, chg24)
        conf_k = ("315" if s >= 75 else "215" if s >= 60 else "155" if s >= 50 else "111")
        return {"coin": coin, "price": ind["close"], "rsi": round(ind["rsi"], 1),
                "vol_x": round(vol_x, 1) if vol_x else 0, "chg": round(chg24, 2),
                "score": s, "rating": rating(s), "qv": qv / 1e6,
                "above_e20": ind["close"] > ind["e20"],
                "macd_bull": ind["macd"] > ind["macd_sig"] and ind["macd"] > 0}
    except Exception:
        return None


def run_daily(token, chat_id):
    t24 = fetch_binance_24h()
    cands = []
    for x in t24:
        s = x["symbol"]
        if not (s.endswith("USDT") and s != "USDTUSDT"):
            continue
        b = s[:-4]
        if "USDT" in b or "FDUSD" in b:
            continue
        try:
            q = float(x["quoteVolume"])
        except (ValueError, KeyError):
            continue
        if q >= MIN_QV:
            cands.append((b, q))
    cands.sort(key=lambda r: -r[1])
    top = [c for c, _ in cands[:80]]

    print(f"[DAILY] candidate pool: {len(top)}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        res = list(ex.map(analyze_coin, top))
    res = [r for r in res if r and r["rating"] in ("BUY", "STRONG BUY")]
    res.sort(key=lambda r: -r["score"])
    res = res[:TOP_N]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sb = [r for r in res if r["rating"] == "STRONG BUY"]
    b = [r for r in res if r["rating"] == "BUY"]
    bar = "\u2501" * 20

    lines = [f"\U0001f4c8 <b>TOP {len(res)} BUY SIGNALS</b> \u00b7 {now}",
             f"\U0001f310 Binance universe \u00b7 {len(top)} candidates scanned "
             f"(vol >$1.5M 24h)",
             ""]

    def row(i, r, badge):
        emoji = f"{badge} <b>{esc(r['coin'])}</b>"
        price = f"${r['price']:,.4f}" if r["price"] < 1 else f"${r['price']:,.2f}"
        vol = f"\U0001f4c8 vol x{r['vol_x']:.1f}" if r["vol_x"] >= 1.2 else \
            f"\U0001f4c9 vol x{r['vol_x']:.1f}"
        dir3 = "\U0001f53b" if r["chg"] >= 0 else "\U0001f53d"
        trend = "E20\u2191 " if r["above_e20"] else ""
        macd = "MACD\u2191 " if r["macd_bull"] else ""
        return (f"{i:>2}. {emoji}  {price}  RSI {r['rsi']:>4}   {vol}   "
                f"{dir3}{r['chg']:+.1f}%   score {r['score']:>2}   {trend}{macd}")

    lines.append(f"\U0001f525 <b>STRONG BUY ({len(sb)})</b>")
    for i, r in enumerate(sb, 1):
        lines.append(row(i, r, "\U0001f7e9"))
    lines.append("")
    lines.append(f"\U0001f44d <b>BUY ({len(b)})</b>")
    for i, r in enumerate(b, len(sb) + 1):
        lines.append(row(i, r, "\U0001f7e1"))
    lines.append("")
    lines.append(bar)
    lines.append("RSI momentum \u00b7 volume spike (1h vs 24h avg) \u00b7 "
                 "MACD/EMA trend \u00b7 score 0-100")
    lines.append("\u26a0\ufe0f Signals only \u2014 verify before trading. "
                 f"{len(sb)} strong, {len(b)} buy from {len(top)} scanned.")

    text = "\n".join(lines)
    print(f"[DAILY] {len(sb)} STRONG BUY, {len(b)} BUY")
    telegram_msg(token, chat_id, text)
    return True


# ────────────────────────── MAIN ──────────────────────────

def main():
    ap = ArgumentParser()
    ap.add_argument("--mode", choices=["arb", "daily"], default="arb")
    ap.add_argument("--all", action="store_true", help="run both modes")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        sys.exit(1)

    if args.all:
        run_arb(token, chat_id)
        run_daily(token, chat_id)
    elif args.mode == "daily":
        run_daily(token, chat_id)
    else:
        run_arb(token, chat_id)


if __name__ == "__main__":
    main()