#!/usr/bin/env python3
"""Crypto-only market data sources and spot order-book helpers."""

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
FEE_TAKER = {"BINANCE": 0.0010, "BITGET": 0.0010, "OKX": 0.0010, "GATE": 0.0015,
             "MEXC": 0.0010, "POLONIEX": 0.0015, "KUCOIN": 0.0010, "HTX": 0.0020}
API_PRIVATE_STATUS = ("BINANCE", "OKX", "MEXC")


def taker_fee(ex):
    return float(os.environ.get(f"FEE_{ex}", FEE_TAKER.get(ex, 0.0020)))


def cost_effective(high, low, high_ex, low_ex):
    fh, fl = taker_fee(high_ex), taker_fee(low_ex)
    cost_mult = (1 + fl) / (1 - fh)
    return ((high * (1 - fh)) / (low * (1 + fl)) - 1) * 100.0, (cost_mult - 1) * 100.0


def calc_net(high, low, high_ex, low_ex):
    return cost_effective(high, low, high_ex, low_ex)[0]


def _norm_levels(pair_list):
    out = []
    for p, q in pair_list:
        try: out.append((float(p), float(q)))
        except (TypeError, ValueError): continue
    return out


def _depth_url(ex, symbol):
    if ex == "BINANCE": return f"{BN}/api/v3/depth?symbol={symbol}USDT&limit=20"
    if ex == "BITGET": return f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={symbol}USDT&type=step0&limit=20"
    if ex == "OKX": return f"https://www.okx.com/api/v5/market/books?instId={symbol}-USDT&sz=20"
    if ex == "GATE": return f"https://api.gateio.ws/api/v4/spot/order_book?currency_pair={symbol}_USDT&limit=20"
    if ex == "MEXC": return f"https://api.mexc.com/api/v3/depth?symbol={symbol}USDT&limit=20"
    if ex == "POLONIEX": return f"https://api.poloniex.com/markets/{symbol}_USDT/orderBook?limit=20"
    if ex == "KUCOIN": return f"https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol={symbol}-USDT"
    if ex == "HTX": return f"https://api.huobi.pro/market/depth?symbol={symbol}usdt&type=step0&depth=20"
    return None


def fetch_orderbook(ex, symbol, limit=20):
    url = _depth_url(ex, symbol)
    if not url: return None
    try:
        d = http_json(url, timeout=12)
        if ex in ("BINANCE", "MEXC", "GATE", "POLONIEX"):
            asks, bids = _norm_levels(d.get("asks", [])), _norm_levels(d.get("bids", []))
        elif ex == "BITGET":
            r = d.get("data", {}); asks, bids = _norm_levels(r.get("asks", [])), _norm_levels(r.get("bids", []))
        elif ex == "OKX":
            r = d.get("data", [{}])[0]; asks, bids = _norm_levels(r.get("asks", [])), _norm_levels(r.get("bids", []))
        elif ex == "KUCOIN":
            r = d.get("data", {}); asks, bids = _norm_levels(r.get("asks", [])), _norm_levels(r.get("bids", []))
        elif ex == "HTX":
            r = d.get("tick", {}); asks, bids = _norm_levels(r.get("asks", [])), _norm_levels(r.get("bids", []))
        else: return None
        asks.sort(key=lambda x: x[0]); bids.sort(key=lambda x: x[0], reverse=True)
        return {"asks": asks, "bids": bids}
    except Exception:
        return None


def slipped_size(levels, base_price, allowed_move):
    if not levels or not base_price: return 0.0
    target = base_price * (1 + allowed_move)
    go_higher = allowed_move >= 0
    qty = cum = 0.0
    for p, q in levels:
        nq = qty + q
        avg = (cum + p * q) / nq if nq else 0
        if (avg >= target) if go_higher else (avg <= target): break
        qty, cum = nq, cum + p * q
    return qty * base_price


def depth_estimate(ex, symbol, orderbook=None, target_net_pct=1.0):
    orderbook = orderbook if orderbook is not None else fetch_orderbook(ex, symbol)
    if not orderbook: return None
    ask0 = orderbook["asks"][0][0] if orderbook["asks"] else None
    bid0 = orderbook["bids"][0][0] if orderbook["bids"] else None
    base = ask0 or bid0
    if not base: return None
    move = max(float(target_net_pct), 0.1) / 100.0
    buy_size = slipped_size(orderbook["asks"], ask0 or base, move)
    sell_size = slipped_size(orderbook["bids"], bid0 or base, -move)
    half = min(slipped_size(orderbook["asks"], ask0 or base, move / 2), slipped_size(orderbook["bids"], bid0 or base, -move / 2))
    return {"full_usd": round(min(buy_size, sell_size)), "half_usd": round(half)}


def currency_name(ex, coin):
    try:
        if ex == "KUCOIN":
            return http_json(f"https://api.kucoin.com/api/v1/currencies/{coin}", timeout=12).get("data", {}).get("fullName")
        if ex == "GATE":
            return http_json(f"https://api.gateio.ws/api/v4/spot/currencies/{coin}", timeout=12).get("name")
        if ex == "POLONIEX":
            d = http_json(f"https://api.poloniex.com/currencies/{coin}", timeout=12); return (d.get("name") or d.get("shortName")) if isinstance(d, dict) else None
        if ex == "HTX":
            for c in http_json("https://api.huobi.pro/v2/reference/currencies", timeout=25).get("data", []):
                if str(c.get("currency", "")).lower() == coin.lower(): return c.get("displayName") or c.get("baseCurrency")
        if ex == "BITGET":
            for c in http_json("https://api.bitget.com/api/v2/spot/public/coins", timeout=25).get("data", []):
                if str(c.get("coin", "")).upper() == coin.upper(): return c.get("coinName")
    except Exception: return None
    return None


def names_diverge(a, b):
    if not a or not b: return False
    na = "".join(ch for ch in a.lower() if ch.isalnum()); nb = "".join(ch for ch in b.lower() if ch.isalnum())
    if not na or not nb or na == nb: return False
    return not (na.startswith(nb) or nb.startswith(na))


def fetch_exchange(name):
    try:
        data = http_json(EXCHANGES[name]); m = {}
        if name == "BINANCE":
            for t in data:
                s=t["symbol"]
                if s.endswith("USDT") and s != "USDTUSDT" and "USDT" not in s[:-4]: m[s[:-4]]=float(t["price"])
        elif name == "BITGET":
            for t in data.get("data", []):
                if t["symbol"].endswith("USDT"): m[t["symbol"][:-4]]=float(t["lastPr"])
        elif name == "OKX":
            for t in data.get("data", []):
                if t["instId"].endswith("-USDT"): m[t["instId"][:-5]]=float(t["last"])
        elif name == "GATE":
            for t in data:
                cp=t["currency_pair"]
                if cp.endswith("_USDT") and "_" not in cp[:-5]: m[cp[:-5]]=float(t["last"])
        elif name == "MEXC":
            for t in data:
                s=t["symbol"]
                if s.endswith("USDT") and s != "USDTUSDT" and "USDT" not in s[:-4]: m[s[:-4]]=float(t["price"])
        elif name == "POLONIEX":
            for t in data:
                if t["symbol"].endswith("_USDT"): m[t["symbol"][:-5]]=float(t["close"])
        elif name == "KUCOIN":
            for t in data["data"]["ticker"]:
                if t["symbol"].endswith("-USDT"): m[t["symbol"][:-5]]=float(t["last"])
        elif name == "HTX":
            for t in data.get("data", []):
                if t["symbol"].endswith("usdt"): m[t["symbol"][:-4].upper()]=float(t["close"])
        return m
    except Exception: return {}


def fetch_binance_24h():
    try: return http_json(f"{BN}/api/v3/ticker/24hr", timeout=25)
    except Exception: return []


def binance_klines(symbol, interval="1h", limit=100):
    try:
        data=http_json(f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}", timeout=15)
        return ([float(k[4]) for k in data], [float(k[2]) for k in data], [float(k[3]) for k in data], [float(k[5]) for k in data])
    except Exception: return [], [], [], []


def binance_price(symbol):
    try: return float(http_json(f"{BN}/api/v3/ticker/price?symbol={symbol}USDT", timeout=10)["price"])
    except Exception: return None


def crypto_quote(qv):
    out={}
    for x in qv:
        s=x.get("symbol", "")
        if not (s.endswith("USDT") and s != "USDTUSDT"): continue
        b=s[:-4]
        if "USDT" in b or "FDUSD" in b or "BUSD" in b: continue
        try: out[b]=float(x.get("quoteVolume",0))
        except (ValueError,TypeError): out[b]=0.0
    return out


def volume_spike(symbol, hours=25):
    try:
        data=http_json(f"{BN}/api/v3/klines?symbol={symbol}USDT&interval=1h&limit={hours}", timeout=20)
        vols=[float(k[5]) for k in data]
        if len(vols)<12: return None
        last=(vols[-1]+vols[-2])/2; avg=sum(vols[:-2])/len(vols[:-2])
        return last/avg if avg>0 else None
    except Exception: return None


def chain_summary(chans):
    open_n=[n for n,d,w in chans if d and w]
    if open_n:
        cap=open_n[:5]; more=f" +{len(open_n)-5}" if len(open_n)>5 else ""
        return " ".join(cap)+more, True
    partial=[f"{n}("+("D" if d else "-")+("W" if w else "-")+")" for n,d,w in chans if d or w]
    return " ".join(partial[:4]) if partial else "all closed", False


def coin_status(coin):
    out={}
    try:
        dd=http_json(f"https://api.kucoin.com/api/v1/currencies/{coin}",timeout=15)["data"]
        out["KUCOIN"]={"dep":bool(dd.get("isDepositEnabled")),"wd":bool(dd.get("isWithdrawEnabled")),"net":["chain"],"note":""}
    except Exception: pass
    try:
        d=http_json(f"https://api.gateio.ws/api/v4/spot/currencies/{coin}",timeout=15)
        chans=[(c.get("name","?"),not c.get("deposit_disabled"),not c.get("withdraw_disabled")) for c in d.get("chains",[])]
        if chans:
            net,fully=chain_summary(chans); out["GATE"]={"dep":fully or any(c[1] for c in chans),"wd":fully or any(c[2] for c in chans),"net":[net],"note":""}
    except Exception: pass
    try:
        for cur in http_json("https://api.huobi.pro/v2/reference/currencies",timeout=25).get("data",[]):
            if cur.get("currency","").lower()==coin.lower():
                chans=[(c.get("displayName","?"),c.get("depositStatus")=="allowed",c.get("withdrawStatus")=="allowed") for c in cur.get("chains",[])]
                if chans:
                    net,fully=chain_summary(chans); out["HTX"]={"dep":fully or any(c[1] for c in chans),"wd":fully or any(c[2] for c in chans),"net":[net],"note":""}
                break
    except Exception: pass
    try:
        for cur in http_json("https://api.bitget.com/api/v2/spot/public/coins",timeout=25).get("data",[]):
            if cur.get("coin","").upper()==coin.upper():
                chans=[(c.get("chain","?"),str(c.get("rechargeable","false")).lower()=="true",str(c.get("withdrawable","false")).lower()=="true") for c in cur.get("chains",[])]
                if chans:
                    net,fully=chain_summary(chans); out["BITGET"]={"dep":fully or any(c[1] for c in chans),"wd":fully or any(c[2] for c in chans),"net":[net],"note":""}
                break
    except Exception: pass
    try:
        for cur in http_json("https://api.poloniex.com/currencies",timeout=25):
            if str(cur.get("currency","")).lower()==coin.lower():
                out["POLONIEX"]={"dep":bool(cur.get("depositEnabled")) and not bool(cur.get("disallowedDeposit")),"wd":bool(cur.get("withdrawalEnabled")) and not bool(cur.get("disallowedWithdraw")),"net":["multi"],"note":""}; break
    except Exception: pass
    for ex in API_PRIVATE_STATUS: out[ex]={"dep":None,"wd":None,"net":[],"note":"API private"}
    return out
