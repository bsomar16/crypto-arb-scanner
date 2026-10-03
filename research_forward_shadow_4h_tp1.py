#!/usr/bin/env python3
"""Research-only TP1 forward shadow for frozen 4h hypotheses."""
from __future__ import annotations
import json, os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from live_replay import fetch_history
from signals import intraday_signal

SYMBOLS=["BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT","LTC","BCH","UNI","NEAR","ATOM","APT","ARB","OP","SUI","INJ"]
INTERVAL="4h"; HOLD_MAX_HOURS=120
STATE_PATH="state/forward_shadow_4h_tp1.json"
CANDIDATES=("baseline","structure_ge_50","base_and_structure_ge_50","non_expansion_and_structure_ge_50")

def candidate_matches(signal:dict[str,Any], candidate:str)->bool:
    expansion=str(signal.get("expansion_state") or "")
    structure=float(signal.get("structure_score",0) or 0)
    if candidate=="baseline": return True
    if candidate=="structure_ge_50": return structure>=50
    if candidate=="base_and_structure_ge_50": return structure>=50 and expansion=="BASE"
    if candidate=="non_expansion_and_structure_ge_50": return structure>=50 and expansion!="EXPANSION"
    raise ValueError(candidate)

def resolve_position(p:dict[str,Any], bars:list[list[Any]])->dict[str,Any]|None:
    entry_time=int(p["candle_open_time"]); entry=float(p["entry"]); stop=float(p["stop"]); target=float(p["t1"])
    future=[b for b in bars if int(b[0])>entry_time]
    if not future: return None
    mfe=float(p.get("mfe_pct",0)); mae=float(p.get("mae_pct",0))
    max_age=HOLD_MAX_HOURS*3600000
    for b in future:
        high,low=float(b[2]),float(b[3]); mfe=max(mfe,(high/entry-1)*100); mae=min(mae,(low/entry-1)*100)
        if low<=stop: return {**p,"status":"CLOSED","outcome":"LOSS","exit_reason":"STOP","exit_price":stop,"closed_candle_open_time":int(b[0]),"mfe_pct":round(mfe,3),"mae_pct":round(mae,3)}
        if high>=target: return {**p,"status":"CLOSED","outcome":"WIN","exit_reason":"TP1","exit_price":target,"closed_candle_open_time":int(b[0]),"mfe_pct":round(mfe,3),"mae_pct":round(mae,3)}
        if int(b[0])-entry_time>=max_age: return {**p,"status":"CLOSED","outcome":"EXPIRED","exit_reason":"MAX_HOLD","exit_price":float(b[4]),"closed_candle_open_time":int(b[0]),"mfe_pct":round(mfe,3),"mae_pct":round(mae,3)}
    return {**p,"status":"OPEN","last_price":float(future[-1][4]),"mfe_pct":round(mfe,3),"mae_pct":round(mae,3)}

def load()->dict[str,Any]:
    try:
        with open(STATE_PATH,encoding="utf-8") as f: return json.load(f)
    except (FileNotFoundError,json.JSONDecodeError): return {"version":1,"research_only":True,"exit_policy":"TP1","candidates":list(CANDIDATES),"positions":[],"runs":0}

def save(s:dict[str,Any]):
    os.makedirs("state",exist_ok=True); tmp=STATE_PATH+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(s,f,ensure_ascii=False,indent=2,sort_keys=True)
    os.replace(tmp,STATE_PATH)

def summary(rows:list[dict[str,Any]],candidate:str)->dict[str,Any]:
    rs=[p for p in rows if p.get("candidate")==candidate]
    closed=[p for p in rs if p.get("status")=="CLOSED" and p.get("outcome") in {"WIN","LOSS"}]
    wins=sum(p.get("outcome")=="WIN" for p in closed)
    return {
        "tracked":len(rs),
        "open":sum(p.get("status")=="OPEN" for p in rs),
        "closed_decisive":len(closed),
        "wins":wins,
        "losses":len(closed)-wins,
        "expired":sum(p.get("outcome")=="EXPIRED" for p in rs),
        "precision_pct":round(100*wins/len(closed),2) if closed else None,
        "sample_ready":len(closed)>=20,
    }

def research_readiness(summaries:dict[str,dict[str,Any]])->dict[str,dict[str,Any]]:
    baseline=summaries.get("baseline",{})
    baseline_precision=baseline.get("precision_pct")
    out={}
    for candidate, metrics in summaries.items():
        precision=metrics.get("precision_pct")
        out[candidate]={
            "sample_ready":bool(metrics.get("sample_ready")),
            "decisive_outcomes":int(metrics.get("closed_decisive",0) or 0),
            "delta_vs_baseline_pp":(
                round(float(precision)-float(baseline_precision),2)
                if precision is not None and baseline_precision is not None else None
            ),
            "forward_review_ready":bool(
                metrics.get("sample_ready")
                and precision is not None
                and baseline_precision is not None
                and float(precision) >= float(baseline_precision)
            ),
        }
    return out

def run_once(cfg:dict[str,Any],state:dict[str,Any])->dict[str,Any]:
    positions=list(state.get("positions") or []); cache={}
    for p in positions:
        if p.get("status")=="OPEN": cache.setdefault(str(p["coin"]).upper(),fetch_history(str(p["coin"]).upper(),INTERVAL,180))
    for i,p in enumerate(positions):
        if p.get("status")=="OPEN":
            x=resolve_position(p,cache[str(p["coin"]).upper()])
            if x: positions[i]=x
    new=0
    for symbol in SYMBOLS:
        bars=cache.get(symbol) or fetch_history(symbol,INTERVAL,180); cache[symbol]=bars; trend=fetch_history(symbol,"1d",200)
        if len(bars)<70 or not trend: continue
        sig=intraday_signal(symbol,interval=INTERVAL,limit=180,min_vol_x=None,min_hour_vol=float(cfg.get("buy_fast_min_hour_vol",75000)),chg24=0,min_potential_pct=float(cfg.get("signal_min_potential_pct",5)),max_potential_pct=float(cfg.get("signal_max_potential_pct",300)),min_score=None,min_rr=None,cfg=deepcopy(cfg),historical_data=bars,historical_trend_data=trend,record_history=False)
        if not sig: continue
        ct=int(sig.get("candle_open_time",0) or 0)
        for candidate in CANDIDATES:
            if ct<=0 or not candidate_matches(sig,candidate): continue
            pid=f"{symbol}-{ct}-{candidate}-TP1"
            if any(str(p.get("position_id"))==pid for p in positions): continue
            positions.append({"position_id":pid,"candidate":candidate,"status":"OPEN","outcome_source":"forward_shadow_tp1","exit_policy":"TP1","opened_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"coin":symbol,"interval":INTERVAL,"candle_open_time":ct,"entry":float(sig["entry"]),"stop":float(sig["stop"]),"t1":float(sig["t1"]),"score":float(sig["score"]),"structure_score":float(sig.get("structure_score",0) or 0),"expansion_state":sig.get("expansion_state"),"mfe_pct":0.0,"mae_pct":0.0})
            new+=1
    state.update({"positions":positions,"last_run_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"runs":int(state.get("runs",0) or 0)+1,"summaries":{c:summary(positions,c) for c in CANDIDATES}})
    state["research_readiness"]=research_readiness(state["summaries"])
    return {"new_positions":new,"summaries":state["summaries"],"research_readiness":state["research_readiness"]}

def main():
    with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
    state=load(); result=run_once(cfg,state); save(state)
    print(json.dumps({"research_only":True,"exit_policy":"TP1","generated_at":state["last_run_at"],"result":result},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
