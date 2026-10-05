#!/usr/bin/env python3
"""Research-only calibration of 4h BUY target/stop geometry.

Replays the current production 4h entry engine, then asks a separate question:
for each detected signal, what would the stop-aware hit rate and expectancy be
if the target were expressed as a multiple of the signal's initial risk?

This never changes production configuration and uses only completed historical
outcomes. If both stop and target are touched in the same candle, stop wins
(conservative OHLC assumption).
"""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean
from live_replay import fetch_history, replay_symbol
from signal_audit import SignalAudit

SYMBOLS=[
    "BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","DOT",
    "LTC","BCH","UNI","NEAR","ATOM","APT","ARB","OP","SUI","INJ",
]
INTERVAL="4h"
WINDOWS=[("oos_1",500,1000),("oos_2",1000,1500),("oos_3",1500,2000),
         ("oos_4",2000,2500),("oos_5",2500,3000)]
R_MULTIPLES=(0.50,0.75,1.00,1.25,1.50,2.00,2.50,3.00)

def classify(rows, r_mult, max_index):
    out=[]
    for row in rows:
        entry=float(row["entry"]); stop=float(row["stop"])
        risk=entry-stop
        if risk<=0: continue
        target=entry + r_mult*risk
        start=int(row["signal_index"])+1
        end=min(int(max_index), start+int(row["horizon_bars"]))
        outcome="EXPIRED"
        exit_i=end-1
        for j in range(start,end):
            high=float(row["_rows"][j][2]); low=float(row["_rows"][j][3])
            if low<=stop:
                outcome="LOSS"; exit_i=j; break
            if high>=target:
                outcome="WIN"; exit_i=j; break
        if outcome=="EXPIRED":
            if end < int(max_index): continue
            last=float(row["_rows"][end-1][4])
            ret=(last/entry-1)*100
        else:
            ret=(stop/entry-1)*100 if outcome=="LOSS" else (target/entry-1)*100
        out.append({"outcome":outcome,"ret_pct":ret,"hold_bars":exit_i-int(row["signal_index"])})
    return out

def stats(rows):
    closed=[x for x in rows if x["outcome"] in ("WIN","LOSS")]
    wins=sum(x["outcome"]=="WIN" for x in closed)
    losses=len(closed)-wins
    avg_ret=mean(x["ret_pct"] for x in closed) if closed else None
    return {
        "signals":len(rows),"closed":len(closed),"wins":wins,"losses":losses,
        "precision_pct":round(100*wins/len(closed),2) if closed else None,
        "avg_closed_return_pct":round(avg_ret,3) if avg_ret is not None else None,
        "sample_met_20_closed":len(closed)>=20,
    }

def main():
    with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
    report=[]; pooled={str(r):[] for r in R_MULTIPLES}
    for name,start,end in WINDOWS:
        trades=[]
        for symbol in SYMBOLS:
            rows=fetch_history(symbol,INTERVAL,max(4500,int(cfg.get("backtest_bars",4500))))
            trend=fetch_history(symbol,"1d",max(500,int(cfg.get("backtest_bars",3000))//8))
            if len(rows)<end: continue
            audit=SignalAudit()
            base=replay_symbol(
                symbol,INTERVAL,rows,trend,start,end,cfg=deepcopy(cfg),audit=audit,
                entry_policy={"require_retest":True,"require_sweep":False,"allow_early_retest":False},
            )
            for t in base:
                t["_rows"]=rows
            trades.extend(base)
        item={"window":name,"start_bar":start,"end_bar":end,"candidates":{}}
        for r in R_MULTIPLES:
            key=str(r)
            z=stats(classify(trades,r,end))
            item["candidates"][key]=z
            pooled[key].extend(classify(trades,r,end))
        report.append(item)
    pooled_out={k:stats(v) for k,v in pooled.items()}
    out={
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only":True,
        "method":"current live-engine 4h replay; stop-aware target multiples of initial risk",
        "risk_multiples":list(R_MULTIPLES),
        "windows":report,
        "pooled":pooled_out,
        "assumption":"If stop and target are both touched in the same OHLC candle, stop is counted first.",
        "warning":"Research-only. No production target, stop, or entry rule is changed.",
    }
    print(json.dumps(out,ensure_ascii=False,indent=2))
    with open("state/oos_4h_outcome_calibration.json","w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=2)

if __name__=="__main__":
    main()
