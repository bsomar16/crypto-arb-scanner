#!/usr/bin/env python3
"""Market data sources: 8 crypto exchanges, Binance mirror (geo-safe), Yahoo stocks."""

import json
import os

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
}

BN = "https://data-api.binance.vision"

# Approximate spot taker fees (fraction of notional). Fees change rarely;
# confirm with each venue before relying on exact numbers. Env-overridable.
FEE_TAKER = {
    "BINANCE": 0.0010, "BITGET": 0.0010, "OKX": 0.0010, "GATE": 0.0015,
    "MEXC": 0.0010, "POLONIEX": 0.0015, "KUCOIN": 0.0010, "HTX": 0.0020,
}

# Exchanges with no public deposit/withdraw status endpoint (need API keys).
API_PRIVATE_STATUS = ("BINANCE", "OKX", "MEXC")


def taker_fee(ex):
    return float(os.environ.get(f"FEE_{ex}", FEE_TAKER.get(ex, 0.0020)))


def cost_effective(high, low, high_ex, low_ex):
    """Return (net_return_pct, round-trip cost fraction) for the arb leg pair."""
    fh, fl = taker_fee(high_ex), taker_fee(low_ex)
    cost_mult = (1 + fl) / (1 - fh)
    return ((high * (1 - fh)) / (low * (1 + fl)) - 1) * 100.0, (cost_mult - 1) * 100.0


def calc_net(high, low, high_ex, low_ex):
    net, _ = cost_effective(high, low, high_ex, low_ex)
    return net


# ─────────────────────────── order book depth ───────────────────────────

def _norm_levels(pair_list):
    out = []
    for p, q in pair_list:
        try:
            out.append((float(p), float(q)))
        except (TypeError, ValueError):
            continue
    return out


def _depth_url(ex, symbol):
    if ex == "BINANCE":
        return f"https://data-api.binance.vision/api/v3/depth?symbol={symbol}USDT&limit=20"
    if ex == "BITGET":
        return f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={symbol}USDT&type=step0&limit=20"
    if ex == "OKX":
        return f"https://www.okx.com/api/v5/market/books?instId={symbol}-USDT&sz=20"
    if ex == "GATE":
        return f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit=20"
    if ex == "MEXC":
        return f"https://api.mexc.com/api/v3/depth?symbol={symbol}USDT&limit=20"
    if ex == "POLONIEX":
        return f"https://api.poloniex.com/markets/{symbol}_USDT/orderBook?limit=20"
    if ex == "KUCOIN":
        return f"https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol={symbol}-USDT"
    if ex == "HTX":
        return f"https://api.huobi.pro/market/depth?symbol={symbol}usdt&type=step0&depth=20"
    return None


def fetch_orderbook(ex, symbol, limit=20):
    url = _depth_url(ex, symbol)
    if not url:
        return None
    try:
        d = http_json(url, timeout=12)
        if ex in ("BINANCE", "MEXC", "GATE"):
            asks = _norm_levels(d.get("asks", []))
            bids = _norm_levels(d.get("bids", []))
        elif ex == "BITGET":
            r = d.get("data", {})
            asks = _norm_levels(r.get("asks", []))
            bids = _norm_levels(r.get("bids", []))
        elif ex == "OKX":
            r = d.get("data", [{}])[0]
            asks = _norm_levels(r.get("asks", []))
            bids = _norm_levels(r.get("bids", []))
        elif ex == "POLONIEX":
            asks = _norm_levels(d.get("asks", []))
            bids = _norm_levels(d.get("bids", []))
        elif ex == "KUCOIN":
            r = d.get("data", {})
            asks = _norm_levels(r.get("asks", []))
            bids = _norm_levels(r.get("bids", []))
        elif ex == "HTX":
            r = d.get("tick", {})
            asks = _norm_levels(r.get("asks", []))
            bids = _norm_levels(r.get("bids", []))
        else:
            return None
        asks.sort(key=lambda x: x[0])
        bids.sort(key=lambda x: x[0], reverse=True)
        return {"asks": asks, "bids": bids}
    except Exception:
        return None


def slipped_size(levels, base_price, allowed_move):
    """Quote-side size (USDT) fillable before price slips `allowed_move`.

    `levels` ascending for asks (buy), descending for bids (sell).
    Pure function, unit-tested.
    """
    if not levels or not base_price:
        return 0.0
    target = base_price * (1 + allowed_move)
    go_higher = allowed_move >= 0
    qty, cum = 0.0, 0.0
    for p, q in levels:
        nq = qty + q
        avg = (cum + p * q) / nq if nq else 0
        crossed = (avg >= target) if go_higher else (avg <= target)
        if crossed:
            break
        qty, cum = nq, cum + p * q
    return qty * base_price


def depth_estimate(ex, symbol, orderbook=None, target_net_pct=1.0):
    """Estimate max size before slippage halves / erases the net spread.

    Returns dict with 'full_usd' and 'half_usd' or None when no book data.
    """
    orderbook = orderbook if orderbook is not None else fetch_orderbook(ex, symbol)
    if not orderbook:
        return None
    ask0 = orderbook["asks"][0][0] if orderbook["asks"] else None
    bid0 = orderbook["bids"][0][0] if orderbook["bids"] else None
    base = ask0 or bid0
    if not base:
        return None
    move = max(float(target_net_pct), 0.1) / 100.0
    buy_size = slipped_size(orderbook["asks"], base, move)
    sell_size = slipped_size(orderbook["bids"], base, -move)
    full = min(buy_size, sell_size)
    half = min(slipped_size(orderbook["asks"], base, move / 2),
               slipped_size(orderbook["bids"], base, -move / 2))
    return {"full_usd": round(full), "half_usd": round(half)}


# ─────────────────────────── symbol identity check ───────────────────────────

def currency_name(ex, coin):
    """Best-effort full name for a coin on an exchange (None if unknown)."""
    try:
        if ex == "KUCOIN":
            d = http_json(f"https://api.kucoin.com/api/v1/currencies/{coin}",
                          timeout=12)
            return d.get("data", {}).get("fullName")
        if ex == "GATE":
            d = http_json(f"https://api.gateio.ws/api/v4/spot/currencies/{coin}",
                          timeout=12)
            return d.get("name")
        if ex == "POLONIEX":
            d = http_json(f"https://api.poloniex.com/currencies/{coin}", timeout=12)
            return (d.get("name") or d.get("shortName")) if isinstance(d, dict) else None
        if ex == "HTX":
            d = http_json("https://api.huobi.pro/v2/reference/currencies", timeout=25)
            for c in d.get("data", []):
                if str(c.get("currency", "")).lower() == coin.lower():
                    return c.get("displayName") or c.get("baseCurrency")
        if ex == "BITGET":
            d = http_json("https://api.bitget.com/api/v2/spot/public/coins", timeout=25)
            for c in d.get("data", []):
                if str(c.get("coin", "")).upper() == coin.upper():
                    return c.get("coinName")
    except Exception:
        return None
    return None


def names_diverge(a, b):
    """True when both names are known and clearly differ (collision check).

    "Aave" vs "Aave (AAVE)" are the same coin (parenthetical suffix), while
    "Bitcoin" vs "Bitcoin Cash" really diverge.
    """
    if not a or not b:
        return False
    na = "".join(ch for ch in a.lower() if ch.isalnum())
    nb = "".join(ch for ch in b.lower() if ch.isalnum())
    if not na or not nb:
        return False
    if na == nb:
        return False
    # parenthetical/quoted suffix -> same base name
    if na.startswith(nb) or nb.startswith(na):
        return False
    return True


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
    try:
        d = http_json("https://api.poloniex.com/currencies", timeout=25)
        for cur in d:
            if str(cur.get("currency", "")).lower() == coin.lower():
                dep = bool(cur.get("depositEnabled")) and not bool(cur.get("disallowedDeposit"))
                wd = bool(cur.get("withdrawalEnabled")) and not bool(cur.get("disallowedWithdraw"))
                out["POLONIEX"] = {"dep": dep, "wd": wd, "net": ["multi"],
                                   "note": ""}
                break
    except Exception:
        pass
    for ex in API_PRIVATE_STATUS:
        out[ex] = {"dep": None, "wd": None, "net": [], "note": "API priv\u00e9e"}
    return out