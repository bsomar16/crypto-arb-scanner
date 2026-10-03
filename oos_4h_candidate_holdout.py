#!/usr/bin/env python3
"""Research-only holdout for the predeclared 4h structure>=50 + BASE candidate."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS=["LTC","LINK","DOT","AVAX","TRX","SUI","TON"]
WINDOWS=[("oos_1",750,1500),("oos_2",1500,2250),("oos_3",2250,3000)]
INTERVAL="4h"

def stats(rows):
    x=summarize(rows)
    return {"signals":x["signals"],"closed":x["closed"],"wins":x["wins"],"losses":x["losses"],
            "precision_pct":round(x["precision_pct"],2) if x["precision_pct"] is not None else None,
            "avg_mfe_pct":round(x["avg_mfe_pct"],3) if x["avg_mfe_pct"] is not None else None,
            "avg_mae_pct":round(x["avg_mae_pct"],3) if x["avg_mae_pct"] is not None else None}

def main():
    with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
    windows=[]; pooled_base=[]; pooled_candidate=[]
    for name,start,end in WINDOWS:
        base=[]; candidate=[]
        for symbol in SYMBOLS:
            rows=fetch_history(symbol,INTERVAL,int(cfg.get("backtest_bars",3000)))
            trend=fetch_history(symbol,"1d",max(500,int(cfg.get("backtest_bars",3000))//8))
            if len(rows)<end: continue
            trades=replay_symbol(symbol,INTERVAL,rows,trend,start,end,cfg=deepcopy(cfg),
                audit=SignalAudit(),entry_policy={"require_retest":True,"require_sweep":False,"allow_early_retest":False})
            base += trades
            candidate += [r for r in trades if float(r.get("structure_score",0))>=50 and str(r.get("expansion_state") or "")=="BASE"]
        b=stats(base); c=stats(candidate)
        c["coverage_pct"]=round(100*len(candidate)/len(base),2) if base else None
        c["delta_precision_pp"]=round(c["precision_pct"]-b["precision_pct"],2) if c["precision_pct"] is not None and b["precision_pct"] is not None else None
        windows.append({"window":name,"start_bar":start,"end_bar":end,"baseline":b,"candidate":c})
        pooled_base += base; pooled_candidate += candidate
    b=stats(pooled_base); c=stats(pooled_candidate)
    c["coverage_pct"]=round(100*len(pooled_candidate)/len(pooled_base),2) if pooled_base else None
    c["delta_precision_pp"]=round(c["precision_pct"]-b["precision_pct"],2) if c["precision_pct"] is not None and b["precision_pct"] is not None else None
    c["sample_met_20_closed"]=c["closed"]>=20
    informative=[w for w in windows if w["baseline"]["closed"]>0 and w["candidate"]["closed"]>0]
    preserved=[w for w in informative if (w["candidate"]["delta_precision_pp"] or 0)>=0]
    out={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"historical_only":True,
         "holdout_assets":SYMBOLS,"candidate":"structure_score >= 50 AND expansion_state == BASE",
         "selection_policy":"Predeclared from prior OOS study; no threshold search.",
         "windows":windows,"pooled":{"baseline":b,"candidate":c},
         "promotion_gate":{"informative_windows":len(informative),"preserved_or_improved_windows":len(preserved),
            "all_informative_windows_preserved_or_improved":bool(informative) and len(informative)==len(preserved),
            "pooled_closed_at_least_20":c["sample_met_20_closed"]},
         "warning":"Research-only. No production rule is changed. A passing holdout still requires fresh forward/shadow validation."}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    with open("state/oos_4h_candidate_holdout.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)

if __name__=="__main__": main()
