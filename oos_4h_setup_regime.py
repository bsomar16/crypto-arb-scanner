#!/usr/bin/env python3
"""Research-only 4h OOS decomposition by setup, regime, expansion and entry trigger."""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = ["BTC","ETH","SOL","BNB","XRP","ADA","DOGE"]
INTERVAL = "4h"
GROUPS = {
    "setup": lambda r: str(r.get("setup_type") or "UNKNOWN"),
    "trend": lambda r: str(r.get("trend_4h") or "UNKNOWN"),
    "expansion": lambda r: str(r.get("expansion_state") or "UNKNOWN"),
    "entry_trigger": lambda r: str(r.get("entry_trigger") or "UNKNOWN"),
    "score_band": lambda r: (
        "50-54" if float(r.get("score",0)) < 55 else
        "55-59" if float(r.get("score",0)) < 60 else
        "60-69" if float(r.get("score",0)) < 70 else
        "70+"
    ),
    "structure_band": lambda r: (
        "0-49" if float(r.get("structure_score",0)) < 50 else
        "50-64" if float(r.get("structure_score",0)) < 65 else
        "65-79" if float(r.get("structure_score",0)) < 80 else
        "80+"
    ),
}

def stats(rows):
    s=summarize(rows)
    return {
        "signals":s["signals"],"closed":s["closed"],"wins":s["wins"],"losses":s["losses"],
        "precision_pct":round(s["precision_pct"],2) if s["precision_pct"] is not None else None,
        "coverage_pct":None,
        "avg_mfe_pct":round(s["avg_mfe_pct"],3) if s["avg_mfe_pct"] is not None else None,
        "avg_mae_pct":round(s["avg_mae_pct"],3) if s["avg_mae_pct"] is not None else None,
        "avg_hold_hours":round(s["avg_hold_hours"],2) if s["avg_hold_hours"] is not None else None,
    }

def main():
    with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
    all_trades=[]
    for symbol in SYMBOLS:
        rows=fetch_history(symbol,INTERVAL,int(cfg.get("backtest_bars",3000)))
        trend=fetch_history(symbol,"1d",max(500,int(cfg.get("backtest_bars",3000))//8))
        if len(rows)<200: continue
        train=min(int(cfg.get("validation_train_bars",1500)),len(rows))
        val=min(int(cfg.get("validation_validation_bars",750)),max(0,len(rows)-train))
        start,end=train+val,len(rows)
        if end-start<50: continue
        audit=SignalAudit()
        trades=replay_symbol(symbol,INTERVAL,rows,trend,start,end,cfg=deepcopy(cfg),audit=audit,
            entry_policy={"require_retest":True,"require_sweep":False,"allow_early_retest":False})
        all_trades.extend(trades)
    baseline=stats(all_trades)
    baseline["coverage_pct"]=100.0
    report_groups={}
    for gname, keyfn in GROUPS.items():
        buckets={}
        for row in all_trades:
            key=keyfn(row)
            buckets.setdefault(key,[]).append(row)
        report_groups[gname]=[]
        for key,rows in sorted(buckets.items()):
            s=stats(rows)
            s["group"]=key
            s["coverage_pct"]=round(100*len(rows)/len(all_trades),2) if all_trades else None
            s["sample_met_20_closed"]=s["closed"]>=20
            s["delta_precision_pp"]=round(s["precision_pct"]-baseline["precision_pct"],2) if s["precision_pct"] is not None else None
            report_groups[gname].append(s)
    report={
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only":True,
        "method":"current live-engine 4h replay, fixed OOS window, descriptive decomposition using predeclared groups",
        "baseline":baseline,
        "groups":report_groups,
        "warning":"Descriptive research only. Groups are not production gates and should not be selected from this report alone.",
        "promotion_rule":"No production change from this study; any candidate requires independent rolling OOS and forward/shadow validation."
    }
    print(json.dumps(report,ensure_ascii=False,indent=2))
    with open("state/oos_4h_setup_regime.json","w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
if __name__=="__main__": main()
