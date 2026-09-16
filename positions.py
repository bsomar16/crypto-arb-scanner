#!/usr/bin/env python3
"""Virtual crypto position tracking for BUY signals and arbitrage follow-up."""

from datetime import datetime, timezone
from botutil import fmt_price, log, esc, env_float, telegram_msg, load_json, save_json
from markets import binance_price
import store
import signal_history

POS_FILE = "state/positions.json"
POSITION_EXPIRY_DEFAULT = 14
MAX_OPEN_DEFAULT = 40


def _cfg_num(cfg, key, env_name, default):
    return env_float(env_name, cfg.get(key, default))


def thresholds(cfg):
    return {"sl": _cfg_num(cfg, "stoploss_pct", "STOPLOSS_PCT", 0.05),
            "tp1": _cfg_num(cfg, "tp1_pct", "TP1_PCT", 0.05),
            "tp2": _cfg_num(cfg, "tp2_pct", "TP2_PCT", 0.10),
            "tp3": _cfg_num(cfg, "tp3_pct", "TP3_PCT", 0.20),
            "expiry_days": _cfg_num(cfg, "position_expiry_days", "POSITION_EXPIRY_DAYS", POSITION_EXPIRY_DEFAULT)}


def compute_levels(entry, t):
    return {"sl": entry*(1-t["sl"]), "tp1": entry*(1+t["tp1"]), "tp2": entry*(1+t["tp2"]), "tp3": entry*(1+t["tp3"])}


def load():
    data=load_json(POS_FILE,[]); return data if isinstance(data,list) else []


def save(positions): save_json(POS_FILE,positions)


def open_picks(picks,cfg,source="daily"):
    """Open virtual long tracking records. Signal-specific SL/TP levels are preferred."""
    t=thresholds(cfg); max_open=int(_cfg_num(cfg,"max_open_positions","MAX_OPEN_POSITIONS",MAX_OPEN_DEFAULT)); positions=load(); open_coins={p["coin"] for p in positions if p.get("status")=="open"}; opened=0
    for r in picks:
        coin=r.get("coin","")
        if not coin or coin in open_coins: continue
        if len(open_coins)>=max_open: break
        try: entry=float(r.get("entry",r["price"]))
        except (TypeError,KeyError,ValueError): continue
        fallback=compute_levels(entry,t)
        sl=float(r.get("stop",fallback["sl"])); tp1=float(r.get("t1",fallback["tp1"])); tp2=float(r.get("t2",fallback["tp2"])); tp3=float(r.get("t3",fallback["tp3"]))
        signal_history.record_signal(r)
        pos={"coin":coin,"entry":entry,"entry_ts":datetime.now(timezone.utc).isoformat(timespec="seconds"),"sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,"tp1_hit":False,"tp2_hit":False,"tp3_hit":False,"status":"open","close_ts":None,"close_price":None,"rating":r.get("rating","BUY"),"score":r.get("score"),"potential_pct":r.get("potential_pct"),"risk_pct":r.get("risk_pct"),"rr":r.get("rr"),"setup_type":r.get("setup_type"),"interval":r.get("interval"),"source":source,"last_price":entry,"last_pct":0.0}
        positions.append(pos); open_coins.add(coin); opened+=1
        store.position_event("open",coin,entry,price=entry,note=f"source={source};setup={r.get('setup_type','daily')}")
    if opened: save(positions); log("POSITIONS",f"opened {opened} virtual crypto longs ({source})")
    return opened


def _fmt_msg(event,coin,pos,price):
    entry=pos["entry"]; pct=(price/entry-1)*100 if entry else 0; e=esc(coin); f=fmt_price
    if event=="sl": return f"🛑 STOP LOSS · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event=="tp1": return f"✅ TP1 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event=="tp2": return f"✅ TP2 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%)"
    if event=="tp3": return f"🏁 TP3 hit · <b>{e}</b> · entry {f(entry)} → {f(price)} ({pct:+.1f}%) · closed"
    if event=="expired": return f"⏰ <b>{e}</b> · position expired after {pos.get('expiry_days','?')}d · last {f(price)}"
    return None


def check_positions(token,chat_id,cfg):
    expiry_days=thresholds(cfg)["expiry_days"]; positions=load()
    if not positions: return 0
    now=datetime.now(timezone.utc); keep=[]; alerts=[]
    for pos in positions:
        if pos.get("status")!="open": continue
        coin=pos.get("coin",""); price=binance_price(coin)
        if price is None: keep.append(pos); continue
        pos["last_price"]=price; entry=pos["entry"]; pos["last_pct"]=round((price/entry-1)*100 if entry else 0,2)
        try: entry_t=datetime.fromisoformat(pos["entry_ts"])
        except (TypeError,ValueError): entry_t=now
        age_days=round((now-entry_t).total_seconds()/86400,1)
        if age_days>=expiry_days:
            pos["status"]="expired"; pos["close_ts"]=now.isoformat(timespec="seconds"); pos["close_price"]=price; pos["expiry_days"]=age_days; alerts.append(_fmt_msg("expired",coin,pos,price)); store.position_event("expired",coin,entry,price=price); signal_history.record_outcome(pos,"EXPIRED",price); continue
        closed=False
        if price<=pos["sl"]:
            pos["status"]="closed_sl"; closed=True; alerts.append(_fmt_msg("sl",coin,pos,price)); store.position_event("sl",coin,entry,price=price); signal_history.record_outcome(pos,"LOSS",price)
        elif not pos.get("tp1_hit") and price>=pos["tp1"]:
            pos["tp1_hit"]=True; alerts.append(_fmt_msg("tp1",coin,pos,price)); store.position_event("tp1",coin,entry,price=price)
        elif not pos.get("tp2_hit") and price>=pos["tp2"]:
            pos["tp2_hit"]=True; alerts.append(_fmt_msg("tp2",coin,pos,price)); store.position_event("tp2",coin,entry,price=price)
        elif not pos.get("tp3_hit") and price>=pos["tp3"]:
            pos["tp3_hit"]=True; pos["status"]="closed_tp3"; closed=True; alerts.append(_fmt_msg("tp3",coin,pos,price)); store.position_event("tp3",coin,entry,price=price,outcome="tp3"); signal_history.record_outcome(pos,"WIN",price)
        if closed:
            pos["close_ts"]=pos.get("close_ts") or now.isoformat(timespec="seconds"); pos["close_price"]=pos.get("close_price") or price; store.position_event("close",coin,entry,price=price,outcome=pos["status"])
        if pos.get("status")=="open": keep.append(pos)
    save(keep)
    for msg in alerts:
        if msg:
            try: telegram_msg(token,chat_id,msg)
            except Exception as e: log("POSITIONS","telegram error:",e)
    return len(alerts)
