"""Chronological SLS Pine-rule evaluator.

This module is deliberately pure: callers provide closed OHLCV bars and
already-aligned, previously closed HTF/BTC context. It never places orders.
"""
from __future__ import annotations
from collections import deque
from sls_pine_math import pine_ema, pine_rsi, pine_macd, pine_atr, confirmed_pivots

THRESHOLDS = {"Conservative": 90, "Balanced": 86, "Aggressive": 82}


def _sma(values, length, i):
    if i + 1 < length:
        return None
    return sum(values[i-length+1:i+1]) / length


def _roc(values, length, i):
    if i < length or values[i-length] == 0:
        return None
    return (values[i] / values[i-length] - 1.0) * 100.0


def _safe(v):
    return v is not None


def _delayed_highest(values, length, delay, index):
    """Pine ta.highest(values, length)[delay] at the requested bar."""
    end = index - delay
    start = end - length + 1
    if start < 0 or end < 0:
        return None
    return max(values[start:end + 1])


def _is_new_confirmed_pivot(pivot_index, pivot_len, bar_index):
    """True only on the bar when a right-confirmed pivot becomes available."""
    return pivot_index + pivot_len == bar_index


def _chronological_bos(opens, closes, confirmed_highs, pivot_len):
    """Return bullish BOS bars using only pivots confirmed by each bar.

    A pivot's price is not available to Pine until its right-side confirmation
    bars have elapsed. Keeping that timing explicit prevents historical
    breakout detection from reading a future-confirmed pivot.
    """
    known = []
    events = []
    last_bos = -10_000
    by_confirmation = {}
    for pivot_index, price in confirmed_highs:
        by_confirmation.setdefault(pivot_index + pivot_len, []).append((pivot_index, price))
    for bar in range(len(closes)):
        known.extend(by_confirmation.get(bar, ()))
        if not known:
            continue
        if closes[bar] > known[-1][1] and closes[bar] > opens[bar] and bar - last_bos >= 5:
            events.append(bar)
            last_bos = bar
    return events


def evaluate_series(rows, interval, htf_rows=None, btc_rows=None,
                    sensitivity="Balanced", min_score=None):
    """Evaluate every closed candle in order; return only a newly triggered BUY.

    HTF/BTC rows must be pre-filtered to candles fully closed no later than
    the primary candle close. This function refuses missing benchmark context.
    """
    if len(rows) < 205:
        return None
    o = [float(x["o"]) for x in rows]; h = [float(x["h"]) for x in rows]
    l = [float(x["l"]) for x in rows]; c = [float(x["c"]) for x in rows]
    v = [float(x["v"]) for x in rows]; q = [float(x.get("q", x["v"]*x["c"])) for x in rows]
    n = len(rows); i = n-1
    e20, e50, e200 = pine_ema(c,20), pine_ema(c,50), pine_ema(c,200)
    rsi = pine_rsi(c,14); ml, ms, mh = pine_macd(c,12,26,9)
    atrs = pine_atr(h,l,c,14)
    if any(x is None for x in (e20[i],e50[i],e200[i],rsi[i],ml[i],ms[i],mh[i],atrs[i])):
        return None
    atr=float(atrs[i]); price=c[i]
    if atr <= 0 or i < 200:
        return None

    tfmin={"5m":5,"15m":15,"1h":60,"4h":240,"1d":1440,"1w":10080}.get(interval)
    if tfmin is None: return None
    small=tfmin<=5; big=tfmin>=1440; huge=tfmin>=10080
    ext_max_atr=5.5 if big else 4.5 if small else 3.5
    ext_max_rsi=92 if big else 90 if small else 82
    max_stop_pct=30.0 if big else 6.5
    turnover_floor=3_000_000 if big else 150_000
    life=12 if huge else 40 if big else 120
    ext_atr=(price-e20[i])/atr
    ext_high=ext_atr>=ext_max_atr or rsi[i]>=ext_max_rsi
    stop=price-1.5*atr; stop_pct=(price-stop)/price*100
    rr=3.0 # TP3 is 4R; Pine's C7 uses target room and stop risk. RR to TP1 = 1.5.
    rr1=1.5
    rr2=2.5

    # Regime classifier
    sep_req=0.0025
    aligned=e20[i]>e50[i]>e200[i]
    above=price>e200[i] and price>e20[i]
    sep_ok=(e20[i]-e50[i])/price>=sep_req and (e50[i]-e200[i])/price>=sep_req
    rsi_rising=rsi[i]>rsi[i-1]
    hist_rising=mh[i]>mh[i-1]
    lowest3=min(c[i-3:i])<e20[i] if i>=3 else False
    if aligned and above and sep_ok and rsi[i]>50: regime="R_BULL"
    elif aligned and above and lowest3 and rsi_rising and hist_rising: regime="R_CONT"
    elif not (aligned and price>e200[i]) and price>e20[i] and rsi[i]>45 and rsi_rising and hist_rising and (_roc(c,5,i) or 0)>0: regime="R_REV"
    elif price<e200[i] and rsi[i]<45 and e50[i]<e200[i]: regime="R_BEAR"
    else: regime="R_SIDE"
    reg_ok=regime in ("R_BULL","R_CONT","R_REV")

    # Previous-closed HTF and BTC context. The caller supplies only completed bars.
    htf_skip=interval=="1w"
    htf_above=htf_align=False
    if htf_skip: htf_score=10
    elif htf_rows and len(htf_rows)>=50:
        hc=[float(x["c"]) for x in htf_rows]
        he20=pine_ema(hc,20); he50=pine_ema(hc,50)
        htf_above=hc[-1]>he50[-1]; htf_align=he20[-1]>he50[-1]
        htf_score=(4 if htf_above else 0)+(6 if htf_align else 0)+(1 if htf_above and not htf_align else 0)
    else: htf_score=0
    btc_state="B_NA"
    if btc_rows and len(btc_rows)>=200:
        bc=[float(x["c"]) for x in btc_rows]
        be50=pine_ema(bc,50); be200=pine_ema(bc,200)
        btc_state="B_SUP" if bc[-1]>be50[-1] and bc[-1]>be200[-1] else "B_BEAR" if bc[-1]<be50[-1] and bc[-1]<be200[-1] else "B_WEAK"
    bench_ok=btc_state!="B_NA"
    bench_pts={"B_SUP":5,"B_WEAK":3.5,"B_BEAR":2}.get(btc_state,0)
    c2=min(15,htf_score+bench_pts)

    # Confirmed pivots; use Pine right-bars confirmation and retain two latest.
    pivot_len=3 if small else 5 if big else 4
    lows=confirmed_pivots(l,pivot_len,pivot_len,True)
    highs=confirmed_pivots(h,pivot_len,pivot_len,False)
    hl=len(lows)>=2 and lows[-1][1]>lows[-2][1]
    hh=len(highs)>=2 and highs[-1][1]>highs[-2][1]
    last_low=lows[-1][1] if lows else None
    last_high=highs[-1][1] if highs else None
    swing_len=50 if big else 30
    res20=max(h[i-20:i]) if i>=20 else None
    discount=bool(last_low is not None and last_high is not None and price<(last_low+last_high)/2)
    bos_bars=_chronological_bos(o,c,highs,pivot_len)
    bos=bool(bos_bars and i-bos_bars[-1]<=10)

    # Stateful OB/FVG zones rebuilt chronologically from closed candles.
    obs=[]; fvgs=[]; equal_lows=[]; sweeps=[]; divergence_state=0
    div_price=div_swing= None; div_index=None; div_vol=div_support=False
    last_ob=last_sweep=-10_000
    low_pivots=[]; high_pivots=[]
    for k in range(i+1):
        # pivots become known only after right-side confirmation
        for p,val in lows:
            if p+pivot_len==k and (not low_pivots or low_pivots[-1][0]!=p): low_pivots.append((p,val))
        for p,val in highs:
            if p+pivot_len==k and (not high_pivots or high_pivots[-1][0]!=p): high_pivots.append((p,val))
        if k>=2 and l[k]>h[k-2] and l[k]-h[k-2]>=.15*(atrs[k] or 0):
            fvgs.append({"top":l[k],"bottom":h[k-2],"born":k})
        if k>=1 and c[k-1]<o[k-1] and c[k]>o[k] and (atrs[k] or 0)>0:
            body=abs(c[k]-o[k])
            rv=v[k]/_sma(v,20,k) if (_sma(v,20,k) or 0)>0 else 0
            prior_bos=any(k-10<=x<=k for x in bos_bars)
            if body>=.8*atrs[k] and rv>=1 and (c[k]>h[k-1] or prior_bos) and k-last_ob>=3:
                obs.append({"top":h[k-1],"bottom":l[k-1],"born":k}); last_ob=k
        obs=[z for z in obs if c[k]>=z["bottom"]][-5:]
        fvgs=[z for z in fvgs if c[k]>=z["bottom"]][-4:]
        # Confirmed SSL levels: 0.35 ATR proximity, five retained, 50-bar expiry.
        # Pair consecutive confirmed pivot lows; compare pivot prices, not later candle lows.
        if len(low_pivots) >= 2 and _is_new_confirmed_pivot(low_pivots[-1][0], pivot_len, k):
            (p1,v1),(p2,v2)=low_pivots[-2],low_pivots[-1]
            if p2-p1 <= 50 and abs(v2-v1) <= 0.35*(atrs[k] or 0):
                # Age liquidity levels from their confirmation bar, not
                # the historical pivot bar (which was not yet knowable).
                equal_lows.append((k,(v1+v2)/2.0))
        equal_lows=[x for x in equal_lows if k-x[0] <= 50][-5:]  # age from confirmation bar
        swept=next((level for level in equal_lows if l[k] < level[1] and c[k] > level[1]),None)
        if swept is not None and k-last_sweep>=10:
            sweeps.append((k,swept[1])); last_sweep=k
        # A divergence is a one-time event on the bar that confirms its
        # second pivot. Do not re-arm the same pivot pair on every later bar:
        # doing so continually resets its age and defeats the 60-bar expiry.
        if len(low_pivots)>=2 and low_pivots[-1][0] + pivot_len == k:
            p1,v1=low_pivots[-2]; p2,v2=low_pivots[-1]
            if 5<=p2-p1<=60 and v2<v1 and rsi[p2] is not None and rsi[p1] is not None and rsi[p2]>rsi[p1]:
                divergence_state=2 if mh[p2] is not None and mh[p1] is not None and mh[p2]>mh[p1] else 1
                div_price=v2; div_swing=max(h[max(0,p2-50):p2+1]); div_index=k
                rvz=(v[p2]/(_sma(v,20,p2) or v[p2])) if p2>=20 else 0
                div_vol=rvz>=1; div_support=abs(v2-(e200[p2] or v2))<=2*(atrs[p2] or 0)
        if divergence_state and div_index is not None:
            if l[k]<div_price or k-div_index>60: divergence_state=0
            elif c[k]>div_swing and k-div_index<=60: divergence_state=3
            # Pine's volume confirmation is a persistent flag once any
            # active divergence bar reaches the 1.3x relative-volume trigger.
            if (v[k]/(_sma(v,20,k) or v[k]))>=1.3: div_vol=True

    recent_sweep=bool(sweeps and i-sweeps[-1][0]<=10)
    in_discount=discount
    near_demand=any(z["bottom"]-.5*atr<=price<=z["top"]+.5*atr for z in obs+fvgs)
    rv20=_sma(v,20,i); rvz=v[i]/rv20 if rv20 else 0
    green=c[i]>o[i]
    up_share=sum(v[k] for k in range(max(0,i-9),i+1) if c[k]>o[k])/max(sum(v[max(0,i-9):i+1]),1e-12)
    green3=sum(1 for k in range(max(0,i-2),i+1) if c[k]>o[k])
    # C1 exact point schedule
    c1=(4 if price>e20[i] else 0)+(4 if e20[i]>e50[i] else 0)+(4 if e50[i]>e200[i] else 0)
    c1+=(3 if e20[i]>e20[i-3] else 0)+(3 if rsi[i]>50 and rsi_rising else 0)+(2 if price>e50[i] else 0)
    # C3
    c3=(4 if ml[i]>ms[i] else 0)+(4 if hist_rising else 0)+(4 if (_roc(c,10,i) or 0)>0 else 0)+(3 if price>c[i-5] else 0)
    # C4
    vol3=_sma(v,3,i); vol10=_sma(v,10,i)
    c4=(3 if rvz>=1 else 0)+(2 if rvz>=1.5 else 0)+(3 if green else 0)+(3 if vol3 and vol10 and vol3>vol10 else 0)+(2 if green3>=2 else 0)+(2 if up_share>.5 else 0)
    # C5
    c5=(4 if (res20 is not None and price>res20) or bos else 0)+(3 if hl else 0)+(2 if hh else 0)
    c5+=(2 if min(c[i-4:i+1])>e50[i] else 0)+(2 if near_demand else 0)+(1 if in_discount else 0)+(1 if recent_sweep else 0)
    # C6 divergence points
    c6={3:6,2:4,1:2}.get(divergence_state,0)+(2 if div_vol else 0)+(2 if div_support else 0)
    c6=min(10,c6)
    # C7: extension, stop validity, room to resistance
    ext_pts=4 if ext_atr<=1.5 else 2 if ext_atr<=2.25 else 0
    rr_valid=stop>0 and stop_pct<=max_stop_pct and rr1>=1.5
    rr_pts=4 if rr_valid and rr1>=1.5 else 1 if rr_valid else 0
    # Pine swingHiGlobal = ta.highest(high, swingLen)[6]. The six-bar
    # offset is intentional: exclude the most recent six candles from the
    # resistance reference to avoid using a still-forming local swing.
    resistance=_delayed_highest(h,swing_len,6,i)
    room_rr=(resistance-price)/(price-stop) if resistance is not None and price>stop else None
    room_pts=(2 if room_rr is not None and room_rr>=1.5 else 0) if resistance is not None else (1 if rr_valid else 0)
    c7=ext_pts+rr_pts+room_pts
    # C8 independent confluence count
    liq_ok=sum(q[max(0,i-19):i+1])>=turnover_floor
    conf=sum([regime in ("R_BULL","R_CONT"),htf_skip or (htf_above and htf_align),
              btc_state=="B_SUP",divergence_state>=2,rvz>=1.5,hl,rr2>=2,
              50<rsi[i]<75,not ext_high,liq_ok,bos,near_demand])
    c8=10 if conf>=10 else 8 if conf>=9 else 6 if conf>=8 else 4 if conf>=7 else 2 if conf>=6 else 0
    score=max(0,min(100,c1+c2+c3+c4+c5+c6+c7+c8))
    threshold=float(min_score if min_score is not None else THRESHOLDS.get(str(sensitivity).title(),86))
    hist_ok=i>(50 if big else 200) and all(_safe(x) for x in (e200[i],e20[i],e50[i],atrs[i]))
    rr_gate=rr_valid and rr1>=1.5
    gates={"history":hist_ok,"score":score>=threshold,"regime":reg_ok,"benchmark":bench_ok,
           "liquidity":liq_ok,"extension":not ext_high,"risk_reward":rr_gate}
    confirmed=all(gates.values())
    # Scanner performs timeframe/candle deduplication; this pure evaluator
    # reports a qualifying confirmed bar and does not fabricate edge history.
    buy=confirmed
    if not buy: return None
    risk=price-stop
    targets=[price+r*x for x in (1.5,2.5,4,6,8)]
    return {"coin":rows[-1].get("symbol"),"interval":interval,"strategy":"SLS",
            "signal_source":"SLS","signal_engine":"Spot Long Signal Engine","strategy_family":"SLS",
            "signal_action":"BUY","entry_trigger":"SLS_CONFIRMED_CLOSED_CANDLE",
            "candle_open_time":rows[-1]["t"],"price":price,"entry":price,"stop":stop,
            "t1":targets[0],"t2":targets[1],"t3":targets[2],"t4":targets[3],"t5":targets[4],
            "target":targets[2],"atr":atr,"score":round(score,2),"st":round(score,2),
            "rsi":round(rsi[i],2),"vol_x":round(rvz,3),"risk_pct":round(stop_pct,3),
            "rr":rr1,"sls_regime":regime,"sls_benchmark":btc_state,
            "sls_components":{"c1":c1,"c2":c2,"c3":c3,"c4":c4,"c5":c5,"c6":c6,"c7":c7,"c8":c8},
            "sls_gates":gates,"sls_conf_count":conf,"sls_sensitivity":sensitivity,
            "sls_threshold":threshold,"risk_reward_ladder":targets,
            "sls_quote_turnover_20":sum(q[max(0,i-19):i+1])}
