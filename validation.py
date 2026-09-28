#!/usr/bin/env python3
"""Out-of-sample validation using the exact live BUY signal engine replay."""
from __future__ import annotations
from datetime import datetime, timezone
from statistics import mean
import json, os
from signal_audit import SignalAudit
from live_replay import fetch_history, replay_symbol, summarize
from signals import STRATEGY_PROFILES

PATH="state/profile_validation.json"

def _profile(interval): return next(p for p in STRATEGY_PROFILES.values() if p["interval"]==interval)
def summarize_near_miss_shadows(rows):
    rows=[r for r in rows if not r.get("censored")]
    out={}
    stages=sorted({r.get("stage") for r in rows if r.get("stage")})
    for stage in stages:
        subset=[r for r in rows if r.get("stage")==stage]
        out[stage]={
            "samples":len(subset),
            "mfe_avg_pct":round(mean(float(r.get("mfe_pct",0)) for r in subset),3) if subset else 0.0,
            "mae_avg_pct":round(mean(float(r.get("mae_pct",0)) for r in subset),3) if subset else 0.0,
            "hit_5_pct":round(sum(bool((r.get("milestones") or {}).get("5")) for r in subset)/len(subset)*100,2) if subset else None,
            "hit_10_pct":round(sum(bool((r.get("milestones") or {}).get("10")) for r in subset)/len(subset)*100,2) if subset else None,
            "hit_20_pct":round(sum(bool((r.get("milestones") or {}).get("20")) for r in subset)/len(subset)*100,2) if subset else None,
            "hit_30_pct":round(sum(bool((r.get("milestones") or {}).get("30")) for r in subset)/len(subset)*100,2) if subset else None,
            "avg_return_pct":round(mean(float(r.get("ret_pct",0)) for r in subset),3) if subset else 0.0,
        }
    return out


def _merge_shadow_summaries(items):
    merged={}
    for item in items:
        for stage,stats in item.items():
            dst=merged.setdefault(stage,{"samples":0,"mfe_sum":0.0,"mae_sum":0.0,"ret_sum":0.0,"hit_5":0,"hit_10":0,"hit_20":0,"hit_30":0})
            n=int(stats.get("samples",0))
            dst["samples"]+=n
            dst["mfe_sum"]+=float(stats.get("mfe_avg_pct",0))*n
            dst["mae_sum"]+=float(stats.get("mae_avg_pct",0))*n
            dst["ret_sum"]+=float(stats.get("avg_return_pct",0))*n
            dst["hit_5"]+=round(float(stats.get("hit_5_pct",0) or 0)*n/100)
            dst["hit_10"]+=round(float(stats.get("hit_10_pct",0) or 0)*n/100)
            dst["hit_20"]+=round(float(stats.get("hit_20_pct",0) or 0)*n/100)
            dst["hit_30"]+=round(float(stats.get("hit_30_pct",0) or 0)*n/100)
    for stage,d in merged.items():
        n=d.pop("samples")
        d["samples"]=n
        d["mfe_avg_pct"]=round(d.pop("mfe_sum")/n,3) if n else 0.0
        d["mae_avg_pct"]=round(d.pop("mae_sum")/n,3) if n else 0.0
        d["avg_return_pct"]=round(d.pop("ret_sum")/n,3) if n else 0.0
        for p in (5,10,20,30):
            d[f"hit_{p}_pct"]=round(d.pop(f"hit_{p}")/n*100,2) if n else None
    return merged


def _split(rows,cfg):
    n=len(rows); train=int(cfg.get("validation_train_bars",max(1000,n//2))); val=int(cfg.get("validation_validation_bars",max(500,n//4)))
    train=min(train,n); val=min(val,max(0,n-train)); return train,train+val,n

def run(cfg=None):
    cfg=dict(cfg or {}); symbols=[str(x).upper() for x in (cfg.get("backtest_symbols") or ["BTC","ETH","SOL","BNB","XRP","ADA","DOGE"])]
    intervals=[x for x in (cfg.get("backtest_intervals") or tuple(p["interval"] for p in STRATEGY_PROFILES.values())) if x in {p["interval"] for p in STRATEGY_PROFILES.values()}]
    bars=int(cfg.get("backtest_bars",3000)); results=[]; splits={}
    for symbol in symbols:
        for interval in intervals:
            rows=fetch_history(symbol,interval,bars); trend_rows=fetch_history(symbol,"4h" if interval in ("5m","15m","1h") else "1d",max(500,bars//8))
            if len(rows)<200: continue
            train_end,val_end,oos_end=_split(rows,cfg); splits[f"{symbol}|{interval}"]={"train_end":train_end,"validation_end":val_end,"oos_end":oos_end}
            split_rows=[]
            for name,start,end in (("train",70,train_end),("validation",train_end,val_end),("oos",val_end,oos_end)):
                if end-start<50: continue
                audit=SignalAudit(); trades=replay_symbol(symbol,interval,rows,trend_rows,start,end,cfg,audit=audit); s=summarize(trades)
                s["audit"]=audit.snapshot(); s["near_miss_shadow"]=summarize_near_miss_shadows(audit.near_miss_shadow_snapshot()); s["detected_signals"]=s["signals"]+s["censored"]; s["evaluated_signals"]=s["signals"]
                split_rows.append({"split":name,**s})
            results.append({"symbol":symbol,"interval":interval,"profile":dict(_profile(interval)),"splits":split_rows})
    by_interval={}
    for interval in intervals:
        oos=[x for r in results if r["interval"]==interval for x in r["splits"] if x["split"]=="oos"]; closed=sum(x["closed"] for x in oos); wins=sum(x["wins"] for x in oos)
        by_interval[interval]={"detected_signals":sum(x["detected_signals"] for x in oos),"evaluated_signals":sum(x["evaluated_signals"] for x in oos),"closed":closed,"wins":wins,
            "near_miss_shadow":_merge_shadow_summaries([x.get("near_miss_shadow",{}) for x in oos]),
            "losses":sum(x["losses"] for x in oos),"expired":sum(x["expired"] for x in oos),"censored":sum(x["censored"] for x in oos),
            "precision_pct":wins/closed*100 if closed else None,"min_sample_met":closed>=int(cfg.get("validation_min_closed_samples",20)),
            "audit_rejections":{k:sum(x.get("audit",{}).get(k,0) for x in oos) for k in sorted({k for x in oos for k in x.get("audit",{}) if k!="qualified"})}}
    report={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"method":"exact live-engine closed-candle replay with train/validation/OOS splits",
        "warning":"Historical precision is descriptive evidence, not a guarantee of future performance.","target_precision_pct":float(cfg.get("validation_target_precision_pct",80)),
        "results":results,"by_interval":by_interval,"splits":splits}
    path=str(cfg.get("validation_stats_path",PATH)); os.makedirs(os.path.dirname(path) or ".",exist_ok=True)
    with open(path,"w",encoding="utf-8") as fh: json.dump(report,fh,ensure_ascii=False,indent=2)
    return report

def format_report(report):
    lines=["EXACT LIVE-ENGINE OOS VALIDATION",f"Generated: {report.get('generated_at')}","Historical evidence only; no future-performance guarantee."]
    for interval,s in report.get("by_interval",{}).items():
        p="-" if s["precision_pct"] is None else f'{s["precision_pct"]:.1f}%'
        lines.append(f'{interval}: detected={s["detected_signals"]} evaluated={s["evaluated_signals"]} closed={s["closed"]} W/L={s["wins"]}/{s["losses"]} expired={s["expired"]} censored={s["censored"]} OOS precision={p} sample_met={s["min_sample_met"]}')
        if s.get("audit_rejections"): lines.append("  rejects: "+" ".join(f"{k}={v}" for k,v in s["audit_rejections"].items()))
    return "\n".join(lines)

if __name__=="__main__": print(format_report(run()))
