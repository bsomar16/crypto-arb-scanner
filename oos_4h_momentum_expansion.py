#!/usr/bin/env python3
"""Research-only OOS test of entry-time 4h continuation hypotheses.

The matrix is frozen before inspecting this run's outcomes:
- baseline
- exclude MOMENTUM x EXPANSION
- require non-EXPANSION
- require structure >= 50 and non-EXPANSION
- require BULLISH trend and non-EXPANSION

No production thresholds or entry policy are changed.
"""
from __future__ import annotations
import json
from copy import deepcopy
from datetime import datetime, timezone
from live_replay import fetch_history, replay_symbol
from signal_audit import SignalAudit

SYMBOLS = [
    "BTC","ETH","SOL","BNB","XRP","ADA","DOGE","AVAX","LINK","TON",
    "TRX","DOT","LTC","AAVE","UNI","SUI","NEAR","TAO","FIL","ATOM",
]
INTERVAL = "4h"
WINDOWS = [("w1",2500,3500),("w2",3500,4500),("w3",4500,5000)]
HOLDOUT_START_MS = int(datetime(2026,10,6,tzinfo=timezone.utc).timestamp()*1000)

CANDIDATES = {
    "baseline": lambda r: True,
    "exclude_momentum_expansion": lambda r: not (
        str(r.get("setup_type") or r.get("setup") or "") == "MOMENTUM"
        and str(r.get("expansion_state") or "") == "EXPANSION"
    ),
    "non_expansion": lambda r: str(r.get("expansion_state") or "") != "EXPANSION",
    "structure_ge_50_non_expansion": lambda r: (
        float(r.get("structure_score",0) or 0) >= 50
        and str(r.get("expansion_state") or "") != "EXPANSION"
    ),
    "bullish_non_expansion": lambda r: (
        str(r.get("trend_4h") or "") == "BULLISH"
        and str(r.get("expansion_state") or "") != "EXPANSION"
    ),
}

def metrics(rows):
    closed=[r for r in rows if r.get("outcome") in ("WIN","LOSS")]
    wins=sum(r.get("outcome")=="WIN" for r in closed)
    losses=len(closed)-wins
    return {
        "signals":len(rows),"closed":len(closed),"wins":wins,"losses":losses,
        "precision_pct":round(100*wins/len(closed),2) if closed else None,
        "coverage_pct":round(100*len(rows)/_BASE_SIGNALS,2) if _BASE_SIGNALS else None,
        "sample_met_20_closed":len(closed)>=20,
    }

def main():
    global _BASE_SIGNALS
    with open("config.json",encoding="utf-8") as f: cfg=json.load(f)
    windows=[]; pooled={k:[] for k in CANDIDATES}
    for name,start,end in WINDOWS:
        trades=[]
        for symbol in SYMBOLS:
            rows=fetch_history(symbol,INTERVAL,6000)
            rows=[r for r in rows if int(r[0]) < HOLDOUT_START_MS]
            if len(rows) < end: continue
            trend=fetch_history(symbol,"1d",max(750,6000//8))
            trend=[r for r in trend if int(r[0]) < HOLDOUT_START_MS]
            audit=SignalAudit()
            trades += replay_symbol(
                symbol,INTERVAL,rows,trend,start,end,cfg=deepcopy(cfg),audit=audit,
                entry_policy={"require_retest":True,"require_sweep":False,"allow_early_retest":False},
            )
        _BASE_SIGNALS=len(trades)
        base=metrics(trades)
        item={"window":name,"start_bar":start,"end_bar":end,"baseline":base,"candidates":{}}
        for cname,pred in CANDIDATES.items():
            selected=[r for r in trades if pred(r)]
            pooled[cname].extend(selected)
            z=metrics(selected)
            z["delta_precision_pp"]=(
                round(z["precision_pct"]-base["precision_pct"],2)
                if z["precision_pct"] is not None and base["precision_pct"] is not None else None
            )
            item["candidates"][cname]=z
        windows.append(item)
    _BASE_SIGNALS=sum(len([]) for _ in [])  # reset; pooled metrics don't use coverage
    pooled_out={}
    for cname,rows in pooled.items():
        closed=[r for r in rows if r.get("outcome") in ("WIN","LOSS")]
        wins=sum(r.get("outcome")=="WIN" for r in closed)
        pooled_out[cname]={
            "signals":len(rows),"closed":len(closed),"wins":wins,"losses":len(closed)-wins,
            "precision_pct":round(100*wins/len(closed),2) if closed else None,
            "sample_met_20_closed":len(closed)>=20,
        }
    out={
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical_only":True,
        "holdout_start_utc":"2026-10-06T00:00:00Z",
        "method":"current production 4h replay across three sequential OOS windows on 20 liquid spot symbols",
        "candidates":list(CANDIDATES.keys()),
        "windows":windows,"pooled":pooled_out,
        "promotion_rule":"Research only. A candidate is not production-eligible unless it preserves/improves precision in every informative independent window, reaches adequate sample, and passes fresh forward/shadow validation.",
        "warning":"No production rule is changed. 90% is a research target, not a guarantee.",
    }
    print(json.dumps(out,ensure_ascii=False,indent=2))
    with open("state/oos_4h_momentum_expansion.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)

if __name__=="__main__":
    main()
