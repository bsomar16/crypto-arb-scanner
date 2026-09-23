#!/usr/bin/env python3
"""Causal BUY context: Zero-Inverse momentum, order blocks and volatility."""
from __future__ import annotations
from statistics import mean
import indicators as ind

def _zero_inverse(closes, fast=5, slow=13, signal=5):
    """Project-local zero-line momentum reversal detector."""
    if len(closes) < slow + signal + 2:
        return {"value":0.0,"signal":0.0,"bullish_reclaim":False,"bullish_reversal":False,"state":"NEUTRAL"}
    ef, es = ind.ema(closes, fast), ind.ema(closes, slow)
    raw = [((f-s)/s*100.0) if s else 0.0 for f,s in zip(ef,es)]
    sig = ind.ema(raw, signal)
    value, prev, sv = raw[-1], raw[-2], sig[-1]
    rising = value > prev
    reclaim = prev <= 0.0 < value
    reversal = prev < 0.0 and value > prev and value >= sv and (value-prev) > 0.08
    state = "BULLISH_REVERSAL" if (reclaim or reversal) else "BULLISH" if value > 0 and rising else "BEARISH" if value < 0 and not rising else "NEUTRAL"
    return {"value":round(value,4),"signal":round(sv,4),"bullish_reclaim":bool(reclaim),"bullish_reversal":bool(reversal),"state":state}

def _order_block(closes, highs, lows, vols, atr):
    """Find the latest bearish candle followed by bullish displacement and local BOS."""
    n=len(closes)
    if n < 30 or atr <= 0:
        return {"bullish":False,"fresh":False,"zone_low":None,"zone_high":None,"distance_pct":None,"strength":0.0,"timeframe_context":"local"}
    avg_vol=mean(vols[-22:-2]) if len(vols)>=22 else mean(vols[:-2])
    best=None
    for i in range(max(5,n-25),n-3):
        if closes[i] >= closes[i-1]: continue
        local_high=max(highs[max(0,i-5):i+1])
        displacement=max(highs[i+1:min(n,i+4)])
        body=abs(closes[i]-closes[i-1])
        if displacement <= local_high or body > atr*1.25 or displacement-closes[i] < atr: continue
        vr=vols[i]/avg_vol if avg_vol>0 else 1.0
        zone_low=lows[i]
        zone_high=max(closes[i],closes[i-1])
        best=(i,zone_low,zone_high,vr,(displacement-closes[i])/atr)
    if not best:
        return {"bullish":False,"fresh":False,"zone_low":None,"zone_high":None,"distance_pct":None,"strength":0.0,"timeframe_context":"local"}
    i,zl,zh,vr,disp=best; price=closes[-1]
    touched=any(lows[j] <= zh and highs[j] >= zl for j in range(i+1,n))
    in_zone=zl <= price <= zh
    dist=abs(price/zh-1)*100 if zh else 999
    strength=min(100.0,35+min(25,disp*10)+min(20,max(0,vr-1)*20)+(20 if in_zone else 0))
    return {"bullish":True,"fresh":not touched or in_zone,"zone_low":round(zl,8),"zone_high":round(zh,8),"distance_pct":round(dist,3),"strength":round(strength,1),"timeframe_context":"local"}

def _volatility(highs,lows,closes,atr):
    """ATR regime: magnitude/context only, never direction."""
    if not closes or atr<=0: return {"atr":0.0,"atr_pct":0.0,"atr_ratio":1.0,"percentile":50.0,"state":"UNKNOWN","expansion":False}
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
    current=mean(trs[-14:]) if len(trs)>=14 else atr
    history=[mean(trs[i-13:i+1]) for i in range(13,len(trs))]
    baseline=mean(history[-50:]) if history else current
    rank=sum(1 for x in history if x<=current)/len(history)*100 if history else 50
    ratio=current/baseline if baseline>0 else 1
    state="EXPANDING" if ratio>=1.12 else "CONTRACTING" if ratio<=0.88 else "NORMAL"
    return {"atr":round(current,8),"atr_pct":round(current/closes[-1]*100,3),"atr_ratio":round(ratio,3),"percentile":round(rank,1),"state":state,"expansion":state=="EXPANDING"}

def build_signal_context(closes,highs,lows,vols,atr,price):
    return {"zero_inverse":_zero_inverse(closes),"order_block":_order_block(closes,highs,lows,vols,atr),"volatility":_volatility(highs,lows,closes,atr)}
