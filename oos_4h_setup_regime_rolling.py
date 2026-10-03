#!/usr/bin/env python3
"""Research-only rolling OOS study of predefined 4h setup/regime combinations."""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS=["BTC","ETH","SOL","BNB","XRP","ADA","DOGE"]
INTERVAL="4h"
WINDOWS=[("oos_1",750,1500),("oos_2",1500,2250),("oos_3",2250,3000)]

def setup(r): return str(r.get("setup") or "").upper()
def expansion(r): return str(r.get("expansion_state") or "").upper()
def structure(r): return float(r.get("structure_score",0) or 0)

CANDIDATES={
 "baseline":lambda r:True,
 "pullback":lambda r:setup(r)=="PULLBACK",
 "base_expansion":lambda r:expansion(r)=="BASE",
 "structure_ge_50":lambda r:structure(r)>=50,
 "pullback_and_base":lambda r:setup(r)=="PULLBACK" and expansion(r)=="BASE",
 "pullback_and_structure_ge_50":lambda r:setup(r)=="PULLBACK" and structure(r)>=50,
 "base_and_structure_ge_50":lambda r:expansion(r)=="BASE" and structure(r)>=50,
 "pullback_base_structure_ge_50":lambda r:setup(r)=="PULLBACK" and expansion(r)=="BASE" and structure(r)>=50,
}

def stats(rows):
 x=summarize(rows)
 return {"signals":x["signals"],"closed":x["closed"],"wins":x["wins"],"losses":x["losses"],
 "precision_pct":round(x["precision_pct"],2) if x["precision_pct"] is not None else None,
 "avg_mfe_pct":round(x["avg_mfe_pct"],3) if x["avg_mfe_pct"] is not None else None,
 "avg_mae_pct":round(x["avg_mae_pct"],3) if x["avg_mae_pct"] is not None else None}

def main():
 with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
 report=[]; pooled={k:[] for k in CANDIDATES}
 for name,start,end in WINDOWS:
  trades=[]
  for symbol in SYMBOLS:
   rows=fetch_history(symbol,INTERVAL,int(cfg.get("backtest_bars",3000)))
   trend_rows=fetch_history(symbol,"1d",max(500,int(cfg.get("backtest_bars",3000))//8))
   if len(rows)<end: continue
   trades += replay_symbol(symbol,INTERVAL,rows,trend_rows,start,end,cfg=deepcopy(cfg),
     audit=SignalAudit(),entry_policy={"require_retest":True,"require_sweep":False,"allow_early_retest":False})
  base=stats(trades); item={"window":name,"start_bar":start,"end_bar":end,"baseline":base,"candidates":{}}
  for cname,pred in CANDIDATES.items():
   selected=[r for r in trades if pred(r)]; pooled[cname].extend(selected); z=stats(selected)
   z["coverage_pct"]=round(100*len(selected)/len(trades),2) if trades else None
   z["delta_precision_pp"]=round(z["precision_pct"]-base["precision_pct"],2) if z["precision_pct"] is not None and base["precision_pct"] is not None else None
   z["sample_met_20_closed"]=z["closed"]>=20; item["candidates"][cname]=z
  report.append(item)
 pooled_out={}
 for cname,rows in pooled.items():
  pooled_out[cname]=stats(rows); pooled_out[cname]["sample_met_20_closed"]=pooled_out[cname]["closed"]>=20
 out={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"historical_only":True,
 "method":"current live-engine 4h replay across three sequential 750-bar OOS windows; predefined setup/regime filters",
 "candidates":list(CANDIDATES),"windows":report,"pooled":pooled_out,
 "promotion_rule":"Candidate must preserve or improve precision in every informative window, have >=20 pooled closed trades, then pass fresh forward/shadow validation.",
 "warning":"Research-only. No production BUY rule is changed."}
 print(json.dumps(out,ensure_ascii=False,indent=2))
 with open("state/oos_4h_setup_regime_rolling.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)

if __name__=="__main__": main()
