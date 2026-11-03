"""Persistent SLS outcome ledger. Research tracking only; never submits orders."""
from botutil import load_json, save_json

PATH = "state/sls_positions.json"
LIFETIME = {"5m":120,"15m":120,"1h":120,"4h":120,"1d":40,"1w":12}


def load_state():
    data=load_json(PATH,{}) or {}
    return data if isinstance(data,dict) else {}


def save_state(state):
    save_json(PATH,state)


def key(coin, interval):
    return f"{str(coin).upper()}:{interval}"


def advance_position(state, coin, interval, rows):
    """Process only newly closed candles for one open SLS position."""
    k=key(coin,interval); p=state.get(k)
    if not p or p.get("status")!="open": return []
    events=[]
    for row in rows or []:
        ts=int(row["t"])
        if ts <= int(p.get("last_bar_open",p["opened_bar_open"])): continue
        # Stop is checked before all targets, matching Pine's source ordering.
        if float(row["l"]) <= float(p["stop"]):
            p.update(status="closed",close_reason="STOP",close_bar_open=ts)
            events.append({"event":"STOP","coin":coin,"interval":interval,"price":p["stop"],"bar_open":ts})
            break
        for n,target in enumerate(p["targets"],1):
            field=f"tp{n}"
            if not p.get(field) and float(row["h"]) >= float(target):
                p[field]=True
                events.append({"event":field.upper(),"coin":coin,"interval":interval,"price":target,"bar_open":ts})
        if all(p.get(f"tp{n}") for n in range(1,6)):
            p.update(status="closed",close_reason="ALL_TARGETS",close_bar_open=ts)
            events.append({"event":"CLOSE","reason":"ALL_TARGETS","coin":coin,"interval":interval,"bar_open":ts})
            break
        # Bar count uses elapsed rows, not wall-clock duration.
        p["bars_elapsed"]=int(p.get("bars_elapsed",0))+1
        if p["bars_elapsed"] > int(p["lifetime_bars"]):
            p.update(status="closed",close_reason="TIMEOUT",close_bar_open=ts)
            events.append({"event":"CLOSE","reason":"TIMEOUT","coin":coin,"interval":interval,"bar_open":ts})
            break
        p["last_bar_open"]=ts
    return events


def register_signal(state, signal):
    """Persist a confirmed SLS BUY once per coin/timeframe while unlocked."""
    if str(signal.get("strategy","")).upper()!="SLS" or str(signal.get("signal_action","BUY")).upper()!="BUY":
        return False
    coin=str(signal.get("coin") or "").upper(); interval=str(signal.get("interval") or "")
    if not coin or not interval: return False
    k=key(coin,interval); old=state.get(k)
    if old and old.get("status")=="open": return False
    entry=float(signal["entry"]); atr=float(signal["atr"]); risk=1.5*atr
    if entry<=0 or atr<=0: return False
    targets=[entry+risk*x for x in (1.5,2.5,4,6,8)]
    state[k]={"status":"open","coin":coin,"interval":interval,
              "entry":entry,"stop":entry-risk,"targets":targets,
              "opened_bar_open":int(signal["candle_open_time"]),
              "last_bar_open":int(signal["candle_open_time"]),
              "bars_elapsed":0,"lifetime_bars":LIFETIME.get(interval,120),
              "tp1":False,"tp2":False,"tp3":False,"tp4":False,"tp5":False,
              "signal_source":"SLS","signal_engine":"Spot Long Signal Engine"}
    return True


def active_coins(state):
    return {p["coin"] for p in state.values() if p.get("status")=="open"}
