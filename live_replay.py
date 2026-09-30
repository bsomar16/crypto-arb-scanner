#!/usr/bin/env python3
"""Exact historical replay of the current live BUY signal engine."""
from __future__ import annotations
from statistics import mean
from botutil import http_json
from signals import STRATEGY_PROFILES, intraday_signal

BN="https://data-api.binance.vision"
INTERVAL_MINUTES={"5m":5,"15m":15,"1h":60,"4h":240,"1d":1440,"1w":10080}
MILESTONES=(5,10,20,30,50,80)

def fetch_history(symbol, interval, bars=3000):
    rows=[]; end=None
    while len(rows)<bars:
        limit=min(1000,bars-len(rows))
        url=f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
        if end is not None: url+=f"&endTime={end}"
        data=http_json(url,timeout=20)
        if not data: break
        batch=list(reversed(data))
        if rows: batch=[x for x in batch if int(x[0])<int(rows[0][0])]
        if not batch: break
        rows=batch+rows; end=int(batch[0][0])-1
        if len(data)<limit: break
    rows.sort(key=lambda x:int(x[0]))
    return rows[:-1] if rows else rows

def _bars_for_hold(interval,hours):
    return max(1,int((hours*60+INTERVAL_MINUTES[interval]-1)//INTERVAL_MINUTES[interval]))

def evaluate_near_miss_outcome(rows, i, diagnostic, horizon, max_index=None):
    """Measure what a rejected near-miss did afterward, without calling it a BUY."""
    entry = float(rows[i][4])
    boundary = len(rows) if max_index is None else min(len(rows), int(max_index))
    end = min(boundary, i + 1 + int(horizon))
    reached = {str(p): False for p in MILESTONES}
    mfe = 0.0
    mae = 0.0
    first_plus_5 = None
    for j in range(i + 1, end):
        high, low = float(rows[j][2]), float(rows[j][3])
        mfe = max(mfe, (high / entry - 1.0) * 100.0)
        mae = min(mae, (low / entry - 1.0) * 100.0)
        for p in MILESTONES:
            if high >= entry * (1 + p / 100):
                reached[str(p)] = True
        if first_plus_5 is None and high >= entry * 1.05:
            first_plus_5 = j - i
    last = float(rows[end - 1][4]) if end > i else entry
    complete = end >= min(len(rows), i + 1 + int(horizon))
    return {
        "stage": diagnostic.get("stage"), "interval": diagnostic.get("interval"),
        "coin": diagnostic.get("coin"), "near_miss_score": float(diagnostic.get("near_miss_score", 0.0) or 0.0),
        "reason": diagnostic.get("reason"), "entry_price": entry,
        "mfe_pct": round(mfe, 3), "mae_pct": round(mae, 3),
        "ret_pct": round((last / entry - 1.0) * 100.0, 3),
        "first_plus_5_bars": first_plus_5, "milestones": reached,
        "hold_bars": end - 1 - i, "window_complete": complete,
        "censored": not complete, "signal_index": i,
    }


def _new_near_miss(before, after):
    """Return a newly recorded diagnostic even when the capped list is full."""
    before_ids = {x.get("event_id") for x in before if x.get("event_id") is not None}
    for item in reversed(after):
        event_id = item.get("event_id")
        if event_id is not None and event_id not in before_ids:
            return item
    if len(after) > len(before):
        old = {repr(x) for x in before}
        for item in after:
            if repr(item) not in old:
                return item
    return None


def evaluate_outcome(rows,i,signal,horizon,max_index=None):
    entry=float(signal["entry"]); stop=float(signal["stop"]); target=float(signal["t3"])
    boundary=len(rows) if max_index is None else min(len(rows),int(max_index))
    end=min(boundary,i+1+horizon)
    reached={str(p):False for p in MILESTONES}; mfe=0.0; mae=0.0
    for j in range(i+1,end):
        high,low=float(rows[j][2]),float(rows[j][3])
        mfe=max(mfe,(high/entry-1)*100); mae=min(mae,(low/entry-1)*100)
        if low<=stop: return {"outcome":"LOSS","exit_i":j,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":j-i,"window_complete":True,"censored":False}
        for p in MILESTONES:
            if high>=entry*(1+p/100): reached[str(p)]=True
        if high>=target: return {"outcome":"WIN","exit_i":j,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":j-i,"window_complete":True,"censored":False}
    last=float(rows[end-1][4]); complete=end>=min(len(rows),i+1+horizon)
    return {"outcome":"EXPIRED","exit_i":end-1,"mfe_pct":mfe,"mae_pct":mae,"milestones":reached,"hold_bars":end-1-i,"ret_pct":(last/entry-1)*100,"window_complete":complete,"censored":not complete}

def replay_symbol(symbol,interval,rows,trend_rows,start_i,end_i,cfg=None,require_complete_outcome=True,audit=None,min_hour_vol=None,min_vol_x=None,min_score=None,min_rr=None,entry_policy=None):
    cfg=dict(cfg or {}); profile=next(p for p in STRATEGY_PROFILES.values() if p["interval"]==interval)
    horizon=_bars_for_hold(interval,int(profile.get("hold_max_hours",24)))
    trades=[]; i=max(70,start_i); boundary=min(end_i,len(rows)-1)
    while i<boundary:
        window=rows[:i+1]; trend_window=[x for x in trend_rows if int(x[0])<=int(rows[i][0])]
        audit_before = audit.near_miss_snapshot() if audit is not None else []
        policy = dict(entry_policy or {})
        replay_cfg = {**cfg, "adaptive_thresholds_enabled": False, "target_optimization_enabled": False}
        if policy:
            replay_cfg["signal_require_retest"] = policy.get("require_retest", True)
            replay_cfg["signal_require_sweep"] = policy.get("require_sweep", False)
            replay_cfg["signal_allow_early_retest"] = policy.get("allow_early_retest", True)
        signal=intraday_signal(symbol,interval=interval,limit=min(180,len(window)),
            min_vol_x=min_vol_x,
            min_hour_vol=float(cfg.get("buy_fast_min_hour_vol",75000) if min_hour_vol is None else min_hour_vol),chg24=0,
            min_potential_pct=float(cfg.get("signal_min_potential_pct",5)),max_potential_pct=float(cfg.get("signal_max_potential_pct",300)),
            min_score=min_score,min_rr=min_rr,cfg=replay_cfg,
            historical_data=window,historical_trend_data=trend_window,record_history=False,audit=audit)
        if signal:
            result=evaluate_outcome(rows,i,signal,horizon,max_index=end_i)
            if require_complete_outcome and result["outcome"]=="EXPIRED" and not result["window_complete"]:
                if audit is not None: audit.reject("censored_outcome")
                i+=1; continue
            result.update(signal); result["signal_index"]=i; result["horizon_bars"]=horizon
            result["estimated_hold_hours"]=result["hold_bars"]*INTERVAL_MINUTES[interval]/60
            trades.append(result); i=max(i+1,result["exit_i"]+1)
        else:
            if audit is not None:
                diagnostic = _new_near_miss(audit_before, audit.near_miss_snapshot())
                if diagnostic is not None and diagnostic.get("stage") in {"liquidity", "volume", "entry_confirmation"}:
                    shadow_horizon = max(1, horizon)
                    shadow = evaluate_near_miss_outcome(rows, i, diagnostic, shadow_horizon, max_index=end_i)
                    if not shadow["censored"] or not require_complete_outcome:
                        audit.record_near_miss_shadow(shadow)
            i+=1
    return trades

def summarize(rows):
    closed=[r for r in rows if r.get("outcome") in ("WIN","LOSS")]
    wins=sum(r.get("outcome")=="WIN" for r in closed); signals=len(rows); censored=sum(bool(r.get("censored")) for r in rows)
    return {"signals":signals,"closed":len(closed),"wins":wins,"losses":len(closed)-wins,"expired":sum(r.get("outcome")=="EXPIRED" for r in rows),"censored":censored,
            "precision_pct":wins/len(closed)*100 if closed else None,
            "milestones":{str(p):{"hits":sum(bool((r.get("milestones") or {}).get(str(p))) for r in rows),"rate_pct":sum(bool((r.get("milestones") or {}).get(str(p))) for r in rows)/signals*100 if signals else None} for p in MILESTONES},
            "avg_potential_pct":mean(float(r["potential_pct"]) for r in rows) if rows else None,"avg_rr":mean(float(r["rr"]) for r in rows) if rows else None,
            "avg_mfe_pct":mean(float(r.get("mfe_pct",0)) for r in rows) if rows else None,"avg_mae_pct":mean(float(r.get("mae_pct",0)) for r in rows) if rows else None,
            "avg_hold_hours":mean(float(r.get("estimated_hold_hours",0)) for r in rows) if rows else None,
            **{f"milestone_{p}_pct":sum(bool((r.get("milestones") or {}).get(str(p))) for r in rows)/signals*100 if signals else None for p in MILESTONES}}
