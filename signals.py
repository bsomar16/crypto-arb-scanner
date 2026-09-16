#!/usr/bin/env python3
"""Crypto signal engine: multi-timeframe scalp + small-trade setups."""

import indicators as ind
from botutil import http_json

BN = "https://data-api.binance.vision"
VALID_INTERVALS = {"5m", "15m", "1h", "4h"}


def rating(s):
    if s >= 85: return "VERY STRONG"
    if s >= 75: return "STRONG BUY"
    if s >= 65: return "BUY"
    if s >= 55: return "WATCH"
    return "AVOID"


def score_daily(dc, vol_ratio, chg24, fng_nudge=0.0):
    s = 0.0
    if dc["close"] > dc["e20"]: s += 15
    if dc["e20"] > dc["e50"]: s += 15
    r = dc["rsi"] or 50
    if 45 <= r <= 68: s += 15
    elif 35 <= r < 45 or 68 < r <= 75: s += 8
    elif 25 <= r < 35 or 75 < r <= 80: s += 3
    if r > 82: s -= 15
    if r < 28: s -= 10
    if dc["macd"] > dc["macd_sig"]: s += 12
    if dc["macd"] > 0: s += 8
    if vol_ratio >= 3: s += 15
    elif vol_ratio >= 2: s += 12
    elif vol_ratio >= 1.5: s += 9
    elif vol_ratio >= 1: s += 5
    elif vol_ratio >= 0.7: s += 2
    if 0 <= chg24 <= 10: s += 10
    elif 10 < chg24 <= 20: s += 6
    elif -5 <= chg24 < 0: s += 4
    elif chg24 > 30: s -= 6
    if dc["macd"] > 0 and dc["macd"] > dc["macd_sig"] and dc["close"] > dc["e20"]: s += 5
    s += fng_nudge
    return max(0.0, min(100.0, s))


def daily_indicators(closes):
    if len(closes) < 15: return None
    e12, e26 = ind.ema(closes, 12), ind.ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ind.ema(macd, 9)
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    return {"rsi": ind.rsi(closes), "macd": macd[-1], "macd_sig": sig[-1], "e20": e20[-1], "e50": e50[-1], "close": closes[-1]}


def analyze_coin_daily(coin, fng_nudge=0.0, min_bars=35, vol_map=None):
    try:
        data = http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval=1d&limit=70", timeout=15)
        if len(data) < min_bars: return None
        closes = [float(k[4]) for k in data][:-1]; highs = [float(k[2]) for k in data][:-1]; lows = [float(k[3]) for k in data][:-1]
        dc = daily_indicators(closes)
        if dc is None: return None
        t24 = http_json(f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT", timeout=15)
        chg24, qv = float(t24["priceChangePercent"]), float(t24["quoteVolume"])
        vols = [float(k[5]) for k in data][:-1]; vol_x = 1.0
        if vol_map and coin in vol_map: vol_x = vol_map[coin] or 1.0
        elif len(vols) >= 12:
            last, avg = (vols[-1] + vols[-2]) / 2, sum(vols[-22:-2]) / len(vols[-22:-2]); vol_x = last / avg if avg > 0 else 0
        s = score_daily(dc, vol_x, chg24, fng_nudge); a = ind.atr(highs, lows, closes)
        return {"coin":coin,"price":dc["close"],"rsi":round(dc["rsi"],1),"vol_x":round(vol_x,2),"chg":round(chg24,2),"vol_x6":round(vol_x,1),"atr":a,"score":round(s,1),"rating":rating(s),"qv":qv/1e6,"above_e20":dc["close"]>dc["e20"],"macd_bull":dc["macd"]>dc["macd_sig"] and dc["macd"]>0}
    except Exception: return None


def _pct(a,b): return (a/b-1.0)*100.0 if b else 0.0


def _fetch_klines(coin, interval, limit):
    data=http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}",timeout=15)
    return data if len(data)>=70 else None


def _trend_filter(coin):
    data=_fetch_klines(coin,"4h",100)
    if not data: return None
    closes=[float(k[4]) for k in data]; e20=ind.ema(closes,20)[-1]; e50=ind.ema(closes,50)[-1]; m,sig,_=ind.macd(closes)
    if closes[-1]>e20>e50 and m[-1]>=sig[-1]: return {"state":"BULLISH","score":12}
    if closes[-1]<e20<e50 and m[-1]<sig[-1]: return {"state":"BEARISH","score":-10}
    return {"state":"MIXED","score":3}


def _setup_type(price,resistance,support,e20,vol_ratio,macd_rising,rsi):
    near_res=resistance>0 and abs(price/resistance-1)<=0.006; near_support=support>0 and abs(price/support-1)<=0.012
    if price>resistance and vol_ratio>=1.25: return "BREAKOUT"
    if price>e20 and near_res and macd_rising: return "MOMENTUM"
    if price>e20 and (near_support or price<=e20*1.012): return "PULLBACK"
    if rsi<45 and macd_rising and near_support: return "REVERSAL"
    return "MOMENTUM"


def intraday_signal(coin, interval="15m", limit=180, min_vol_x=1.15, min_hour_vol=0, chg24=None, min_potential_pct=5.0, max_potential_pct=80.0, min_score=55, min_rr=1.5):
    """Generate a 5m/15m/1h setup; 24h volume is a liquidity-universe filter, not a BUY trigger."""
    if interval not in {"5m","15m","1h"}: return None
    try:
        data=_fetch_klines(coin,interval,limit)
        if not data: return None
        data=data[:-1]
        closes=[float(k[4]) for k in data]; highs=[float(k[2]) for k in data]; lows=[float(k[3]) for k in data]; vols=[float(k[5]) for k in data]; qvols=[float(k[7]) for k in data]
        if len(closes)<70 or (min_hour_vol and sum(qvols[-4:])<min_hour_vol): return None
        e9=ind.ema(closes,9); e20=ind.ema(closes,20); e21=ind.ema(closes,21); m,sig,hist=ind.macd(closes); r=ind.rsi(closes,14) or 50; a=ind.atr(highs,lows,closes) or closes[-1]*0.01; price=closes[-1]
        recent_vol=sum(vols[-2:])/2; base_vol=sum(vols[-22:-2])/20; vol_ratio=recent_vol/base_vol if base_vol>0 else 0
        if vol_ratio<min_vol_x: return None
        resistance=max(highs[-21:-1]); support=min(lows[-21:-1]); range_high=max(highs[-12:-1]); macd_rising=hist[-1]>hist[-2]; ema_bull=e9[-1]>e21[-1] and price>e20[-1]
        trend=_trend_filter(coin)
        if not trend: return None
        setup=_setup_type(price,resistance,support,e20[-1],vol_ratio,macd_rising,r)
        near_support=abs(price/support-1)<=0.012 if support else False
        if setup=="REVERSAL": structure_score=14 if near_support else 5
        elif setup=="BREAKOUT": structure_score=22 if price>=range_high else 12
        elif setup=="PULLBACK": structure_score=18
        else: structure_score=15
        score=0.0; reasons=[]
        if ema_bull: score+=18; reasons.append("EMA structure bullish")
        elif price>e20[-1]: score+=10; reasons.append("price above EMA20")
        else: score-=5
        if m[-1]>sig[-1]: score+=12; reasons.append("MACD bullish")
        if macd_rising: score+=7; reasons.append("MACD histogram rising")
        if 48<=r<=70: score+=10; reasons.append("RSI healthy")
        elif 40<=r<48: score+=5; reasons.append("RSI recovering")
        elif r>78: score-=8; reasons.append("RSI extended")
        if vol_ratio>=2: score+=15; reasons.append(f"volume x{vol_ratio:.1f}")
        elif vol_ratio>=1.5: score+=11; reasons.append(f"volume x{vol_ratio:.1f}")
        else: score+=7; reasons.append(f"volume x{vol_ratio:.1f}")
        score+=structure_score+trend["score"]
        reasons.append(f"4h {trend['state'].lower()}")
        score=max(0,min(100,score))
        stop_dist=max({"5m":1.25,"15m":1.5,"1h":1.8}[interval]*a,price*0.006); stop=price-stop_dist; risk_pct=_pct(price,stop)
        structure_target=resistance if resistance>price else range_high; volatility_target=price+{"5m":3,"15m":4,"1h":5}[interval]*a; target=max(structure_target,volatility_target); potential=_pct(target,price)
        if potential<min_potential_pct: return None
        potential=min(float(max_potential_pct),potential); target=price*(1+potential/100); rr=potential/risk_pct if risk_pct>0 else 0
        if score<min_score or rr<min_rr: return None
        if trend["state"]=="BEARISH" and setup!="REVERSAL": return None
        return {"coin":coin,"interval":interval,"price":price,"entry":price,"stop":stop,"t1":price+stop_dist,"t2":price+2*stop_dist,"t3":target,"rsi":round(r,1),"vol_x":round(vol_ratio,2),"chg24":round(float(chg24 or 0),2),"st":round(score,1),"score":round(score,1),"potential_pct":round(potential,1),"risk_pct":round(risk_pct,2),"rr":round(rr,2),"atr":round(a,6),"resistance":resistance,"support":support,"setup_type":setup,"trend_4h":trend["state"],"e9_e21":e9[-1]>e21[-1],"macd_rising":macd_rising,"reasons":reasons}
    except Exception: return None
