#!/usr/bin/env python3
"""Market data sources: 9 crypto exchanges, Binance mirror (geo-safe), Yahoo stocks."""

import json
from botutil import http_json

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

BN = "https://data-api.binance.vision"


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
        return http_json(f"{BN}/api/v3/ticker/24hr", timeout=25)
    except Exception:
        return []


def binance_klines(symbol, interval="1h", limit=100):
    try:
        data = http_json(
            f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}",
            timeout=15)
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        vols = [float(k[5]) for k in data]
        return closes, highs, lows, vols
    except Exception:
        return [], [], [], []


def binance_price(symbol):
    try:
        d = http_json(f"{BN}/api/v3/ticker/price?symbol={symbol}USDT", timeout=10)
        return float(d["price"])
    except Exception:
        return None


def crypto_quote(qv):
    """From Binance 24h list pick {symbol: quoteVolumeUsd}."""
    out = {}
    for x in qv:
        s = x.get("symbol", "")
        if not (s.endswith("USDT") and s != "USDTUSDT"):
            continue
        b = s[:-4]
        if "USDT" in b or "FDUSD" in b or "BUSD" in b:
            continue
        try:
            out[b] = float(x.get("quoteVolume", 0))
        except (ValueError, TypeError):
            out[b] = 0.0
    return out


def volume_spike(symbol, hours=25):
    try:
        data = http_json(
            f"{BN}/api/v3/klines?symbol={symbol}USDT&interval=1h&limit={hours}",
            timeout=20)
        vols = [float(k[5]) for k in data]
        if len(vols) < 12:
            return None
        last = (vols[-1] + vols[-2]) / 2
        avg = sum(vols[:-2]) / len(vols[:-2])
        return last / avg if avg > 0 else None
    except Exception:
        return None


def yahoo_chart(symbol, interval="1d", rng="2y"):
    """Return dict of lists from Yahoo Finance chart endpoint (works w/o API key)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}" \
              f"?interval={interval}&range={rng}"
        data = http_json(url, timeout=15)
        res = data["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        close = [x if x is not None else 0.0 for x in q.get("close", [])]
        high = [x if x is not None else 0.0 for x in q.get("high", [])]
        low = [x if x is not None else 0.0 for x in q.get("low", [])]
        vol = [x if x is not None else 0.0 for x in q.get("volume", [])]
        meta = res.get("meta", {})
        out = {"close": close, "high": high, "low": low, "volume": vol,
               "price": float(meta.get("regularMarketPrice") or 0),
               "symbol": meta.get("symbol", symbol),
               "chart_name": meta.get("longName") or meta.get("shortName") or symbol}
        return out
    except Exception:
        return None


def yahoo_quote(symbol):
    c = yahoo_chart(symbol, interval="1d", rng="1mo")
    if c:
        return c["price"]
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


def coin_status(coin):
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