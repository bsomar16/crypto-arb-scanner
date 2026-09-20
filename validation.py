#!/usr/bin/env python3
"""Out-of-sample validation using the exact live BUY signal engine replay."""
from __future__ import annotations
from datetime import datetime, timezone
import json, os
from live_replay import fetch_history, replay_symbol, summarize
from signals import STRATEGY_PROFILES

PATH="state/profile_validation.json"
def _profile(interval): return next(p for p in STRATEGY_PROFILES.values() if p["interval"]==interval)
def _split(rows,cfg):
    n=len(rows); train=int(cfg.get("validation_train_bars", max(1000,n//2))); val=int(cfg.get("validation_validation_bars", max(500,n//4)))
    train=min(train,n); val=min(val,max(0,n-train)); return train,train+val,n
def run(cfg=None):
    cfg=dict(cfg or {}); symbols=[str(x).upper() for x in (cfg.get("backtest_symbols") or ["BTC","ETH","SOL"])]
    intervals=[x for x in (cfg.get("backtest_intervals") or tuple(p["interval"] for p in STRATEGY_PROFILES.values())) if x in {p["interval"] for p in STRATEGY_PROFILES.values()}]
    bars=int(cfg.get("backtest_bars",3000)); results=[]; splits={}
    for symbol in symbols:
        for interval in intervals:
            rows=fetch_history(symbol,interval,bars); trend_rows=fetch_history(symbol,"4h" if interval in ("5m","15m","1h") else "1d",max(500,bars//8))
            if len(rows)<200: continue
            train_end,val_end,oos_end=_split(rows,cfg); splits[f"{symbol}|{interval}"]={"train_end":train_end,"validation_end":val_end,"oos_end":oos_end}
            # Replay every split with identical live-engine rules. We report OOS
            # separately; training/validation are context, never mixed into OOS metrics.
            split_rows=[]
            for name,start,end in (("train",100,end_train if False else train_end),("validation",train_end,val_end),("oos",val_end,oos_end)):
                if end-start<50: continue
                trades=replay_symbol(symbol,interval,rows,trend_rows,start,end,cfg)
                s=summarize(trades); split_rows.append({"split":name,**s})
            results.append({"symbol":symbol,"interval":interval,"profile":dict(_profile(interval)),"splits":split_rows})
    by_interval={}
    for interval in intervals:
        oos=[x for r in results if r["interval"]==interval for x in r["splits"] if x["split"]=="oos"]
        closed=sum(x["closed"] for x in oos); wins=sum(x["wins"] for x in oos); signals=sum(x["signals"] for x in oos)
        by_interval[interval]={"signals":signals,"closed":closed,"wins":wins,"losses":sum(x["losses"] for x in oos),"expired":sum(x["expired"] for x in oos),"precision_pct":wins/closed*100 if closed else None,"min_sample_met":closed>=int(cfg.get("validation_min_closed_samples",20))}
    report={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"method":"exact live-engine closed-candle replay with train/validation/OOS splits","warning":"Historical precision is descriptive evidence, not a guarantee of future performance.","target_precision_pct":float(cfg.get("validation_target_precision_pct",80)),"results":results,"by_interval":by_interval,"splits":splits}
    path=str(cfg.get("validation_stats_path",PATH)); os.makedirs(os.path.dirname(path) or ".",exist_ok=True)
    with open(path,"w",encoding="utf-8") as fh: json.dump(report,fh,ensure_ascii=False,indent=2)
    return report
def format_report(report):
    lines=["EXACT LIVE-ENGINE OOS VALIDATION",f"Generated: {report.get('generated_at')}","Historical evidence only; no future-performance guarantee."]
    for interval,s in report.get("by_interval",{}).items():
        p="-" if s["precision_pct"] is None else f'{s["precision_pct"]:.1f}%'
        lines.append(f'{interval}: signals={s["signals"]} closed={s["closed"]} W/L={s["wins"]}/{s["losses"]} expired={s["expired"]} OOS precision={p} sample_met={s["min_sample_met"]}')
    return "\n".join(lines)
if __name__=="__main__": print(format_report(run()))
