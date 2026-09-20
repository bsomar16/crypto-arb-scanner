#!/usr/bin/env python3
"""Exact historical replay of the current live BUY signal engine."""
from __future__ import annotations
from statistics import mean
from botutil import http_json
from signals import STRATEGY_PROFILES, intraday_signal
BN = "https://data-api.binance.vision"
INTERVAL_MINUTES = {"5m":5,"15m":60/4,"1h":60,"4h":240,"1d":1440,"1w":10080}
MILESTONES = (5,10,20,30,50,80)
def fetch_history(symbol, interval, bars=3000):
    rows=[]; end=None
    while len(rows)<bars:
        limit=min(1000,bars-len(rows)); url=f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
        if end is not None: url += f"&endTime={end}"
        data=http_json(url,timeout=20)
        if not data: break
        batch=list(reversed(data))
        if rows: batch=[x for x in batch if int(x[0])<int(rows[0][0])]
        if not batch: break
        rows=batch+rows; end=int(batch[0][0])-1
        if len(data)<limit: break
    rows.sort(key=lambda x:int(x[0])); return rows[:-1] if rows else rows
def _trend_interval(interval): return "4h" if interval in ("5m","15m","1h") else "1d"
def _bars_for_hold(interval, hours): return max(1,int((hours*60+INTERVAL_MINUTES[interval]-1)//INTERVAL_MINUTES[interval]))
def evaluate_outcome(rows, i, signal, horizon):
    entry=float(signal["entry"]); stop=float(signal["stop"]); target=float(signal["t3"]); end=min(len(rows),i+1+horizon)
    reached={str(p):False for p in MILESTONES}; mfe=0.0; mae=0.0
    for j in range(i+1,end):
        high,low=float(rows[j][2]),float(rows[j][3]); mfe=max(mfe,(high/entry-1)*100); mae=min(mae,(low/entry-1)*100)
        if low<=stop: return {"outcome":"LOSS","exit_i":j,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":j-i}
        for p in MILESTONES:
            if high>=entry*(1+p/100): reached[str(p)]=True
        if high>=target: return {"outcome":"WIN","exit_i":j,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":j-i}
    last=float(rows[end-1][4]); return {"outcome":"EXPIRED","exit_i":end-1,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":end-1-i,"ret_pct":(last/entry-1)*100}
def replay_symbol(symbol, interval, rows, trend_rows, start_i, end_i, cfg=None):
    cfg=dict(cfg or {}); profile=next(p for p in STRATEGY_PROFILES.values() if p["interval"]==interval)
    horizon=_bars_for_hold(interval,int(profile.get("hold_max_hours",24))); trades=[]; i=max(70,start_i)
    while i<min(end_i,len(rows)-2):
        window=rows[:i+1]; trend_window=[x for x in trend_rows if int(x[0])<=int(rows[i][0])]
        signal=intraday_signal(symbol,interval=interval,limit=min(180,len(window)),min_vol_x=None,min_hour_vol=0,chg24=0,min_potential_pct=float(cfg.get("signal_min_potential_pct",5)),max_potential_pct=float(cfg.get("signal_max_potential_pct",300)),min_score=None,min_rr=None,cfg={**cfg,"adaptive_thresholds_enabled":False,"target_optimization_enabled":False},historical_data=window,historical_trend_data=trend_window,record_history=False)
        if signal:
            result=evaluate_outcome(rows,i,signal,horizon); result.update(signal); result["signal_index"]=i; result["horizon_bars"]=horizon; result["estimated_hold_hours"]=result["hold_bars"]*INTERVAL_MINUTES[interval]/60; trades.append(result); i=max(i+1,result["exit_i"]+1)
        else: i+=1
    return trades
def summarize(rows):
    closed=[r for r in rows if r.get("outcome") in ("WIN","LOSS")]; wins=sum(r.get("outcome")=="WIN" for r in closed); signals=len(rows)
    return {"signals":signals,"closed":len(closed),"wins":wins,"losses":len(closed)-wins,"expired":sum(r.get("outcome")=="EXPIRED" for r in rows),"precision_pct":wins/len(closed)*100 if closed else None,"milestones":{str(p):{"hits":sum(bool((r.get("milestones") or {}).get(str(p))) for r in rows),"rate_pct":sum(bool((r.get("milestones") or {}).get(str(p))) for r in rows)/signals*100 if signals else None} for p in MILESTONES},"avg_potential_pct":mean(float(r["potential_pct"]) for r in rows) if rows else None,"avg_rr":mean(float(r["rr"]) for r in rows) if rows else None,"avg_mfe_pct":mean(float(r.get("mfe_pct",0)) for r in rows) if rows else None,"avg_mae_pct":mean(float(r.get("mae_pct",0)) for r in rows) if rows else None,"avg_hold_hours":mean(float(r.get("estimated_hold_hours",0)) for r in rows) if rows else None}