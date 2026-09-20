#!/usr/bin/env python3
"""Crypto-only signal engine: multi-timeframe scalp + small-trade setups."""

import indicators as ind
from botutil import http_json
import signal_history
from discovery import structure_snapshot
from entry_engine import evaluate_entry
from expansion import classify_expansion
from adaptive import adaptive_thresholds
from target_quality import optimize_targets

BN = "https://data-api.binance.vision"
VALID_INTERVALS = {"5m", "15m", "1h", "4h", "1d", "1w"}

# Strategy-specific BUY profiles. 24h volume remains a liquidity filter only.\n# Recall is intentionally relaxed modestly; core entry confirmation, R:R, target\n# envelope, regime protection, and outcome-aware adaptive screening remain hard gates.\n# These are not an 80% win-rate guarantee; precision is validated out-of-sample.
STRATEGY_PROFILES = {
    "scalp_5m": {"kind": "Scalp", "interval": "5m", "min_vol_x": 1.05, "min_score": 52, "min_rr": 1.60, "stop_atr": 1.15, "target_atr": 3.0},
    "scalp_15m": {"kind": "Scalp", "interval": "15m", "min_vol_x": 1.00, "min_score": 50, "min_rr": 1.50, "stop_atr": 1.40, "target_atr": 4.0},
    "medium_1h": {"kind": "Medium", "interval": "1h", "min_vol_x": 1.00, "min_score": 50, "min_rr": 1.50, "stop_atr": 1.80, "target_atr": 5.5, "hold_min_hours": 2, "hold_max_hours": 36},
    "medium_4h": {"kind": "Medium", "interval": "4h", "min_vol_x": 0.95, "min_score": 50, "min_rr": 1.50, "stop_atr": 2.00, "target_atr": 5.0, "hold_min_hours": 8, "hold_max_hours": 120},
    "long_1d": {"kind": "Long", "interval": "1d", "min_vol_x": 0.90, "min_score": 52, "min_rr": 1.60, "stop_atr": 2.20, "target_atr": 6.0, "hold_min_hours": 48, "hold_max_hours": 504},
    "long_1w": {"kind": "Long", "interval": "1w", "min_vol_x": 0.85, "min_score": 55, "min_rr": 1.80, "stop_atr": 2.50, "target_atr": 8.0, "hold_min_hours": 168, "hold_max_hours": 2016},
}


def strategy_profile(interval):
    return next((p for p in STRATEGY_PROFILES.values() if p["interval"] == interval), None)


def rating(s):
    if s >= 85: return "VERY STRONG"
    if s >= 75: return "STRONG BUY"
    if s >= 65: return "BUY"
    if s >= 55: return "WATCH"
    return "AVOID"


def score_daily(dc, vol_ratio, chg24, fng_nudge=0.0):
    s = 0.0
    if dc["close"] > dc["e20"]: s += 15
    if dc["e20"] > dc["e50"]: s += 15
    r = dc["rsi"] or 50
    if 45 <= r <= 68: s += 15
    elif 35 <= r < 45 or 68 < r <= 75: s += 8
    elif 25 <= r < 35 or 75 < r <= 80: s += 3
    if r > 82: s -= 15
    if r < 28: s -= 10
    if dc["macd"] > dc["macd_sig"]: s += 12
    if dc["macd"] > 0: s += 8
    if vol_ratio >= 3: s += 15
    elif vol_ratio >= 2: s += 12
    elif vol_ratio >= 1.5: s += 9
    elif vol_ratio >= 1: s += 5
    elif vol_ratio >= 0.7: s += 2
    if 0 <= chg24 <= 10: s += 10
    elif 10 < chg24 <= 20: s += 6
    elif -5 <= chg24 < 0: s += 4
    elif chg24 > 30: s -= 6
    if dc["macd"] > 0 and dc["macd"] > dc["macd_sig"] and dc["close"] > dc["e20"]: s += 5
    s += fng_nudge
    return max(0.0, min(100.0, s))


def daily_indicators(closes):
    if len(closes) < 15: return None
    e12, e26 = ind.ema(closes, 12), ind.ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ind.ema(macd, 9)
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    return {"rsi": ind.rsi(closes), "macd": macd[-1], "macd_sig": sig[-1], "e20": e20[-1], "e50": e50[-1], "close": closes[-1]}


def analyze_coin_daily(coin, fng_nudge=0.0, min_bars=35, vol_map=None):
    try:
        data = http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval=1d&limit=70", timeout=15)
        if len(data) < min_bars: return None
        closes = [float(k[4]) for k in data][:-1]; highs = [float(k[2]) for k in data][:-1]; lows = [float(k[3]) for k in data][:-1]
        dc = daily_indicators(closes)
        if dc is None: return None
        t24 = http_json(f"{BN}/api/v3/ticker/24hr?symbol={coin}USDT", timeout=15)
        chg24, qv = float(t24["priceChangePercent"]), float(t24["quoteVolume"])
        vols = [float(k[5]) for k in data][:-1]; vol_x = 1.0
        if vol_map and coin in vol_map: vol_x = vol_map[coin] or 1.0
        elif len(vols) >= 12:
            last, avg = (vols[-1] + vols[-2]) / 2, sum(vols[-22:-2]) / len(vols[-22:-2]); vol_x = last / avg if avg > 0 else 0
        s = score_daily(dc, vol_x, chg24, fng_nudge); a = ind.atr(highs, lows, closes)
        return {"coin":coin,"price":dc["close"],"rsi":round(dc["rsi"],1),"vol_x":round(vol_x,2),"chg":round(chg24,2),"vol_x6":round(vol_x,1),"atr":a,"score":round(s,1),"rating":rating(s),"qv":qv/1e6,"above_e20":dc["close"]>dc["e20"],"macd_bull":dc["macd"]>dc["macd_sig"] and dc["macd"]>0}
    except Exception: return None


def _pct(a,b): return (a/b-1.0)*100.0 if b else 0.0


def _fetch_klines(coin, interval, limit):
    data=http_json(f"{BN}/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}",timeout=15)
    return data if len(data)>=70 else None


def _trend_filter(coin, interval="15m"):
    trend_interval = "4h" if interval in ("5m", "15m", "1h") else "1d"
    data=_fetch_klines(coin,trend_interval,100)
    if not data: return None
    closes=[float(k[4]) for k in data]; e20=ind.ema(closes,20)[-1]; e50=ind.ema(closes,50)[-1]; m,sig,_=ind.macd(closes)
    if closes[-1]>e20>e50 and m[-1]>=sig[-1]: return {"state":"BULLISH","score":12,"interval":trend_interval}
    if closes[-1]<e20<e50 and m[-1]<sig[-1]: return {"state":"BEARISH","score":-10,"interval":trend_interval}
    return {"state":"MIXED","score":3,"interval":trend_interval}


def _setup_type(price,resistance,support,e20,vol_ratio,macd_rising,rsi):
    near_res=resistance>0 and abs(price/resistance-1)<=0.006; near_support=support>0 and abs(price/support-1)<=0.012
    if price>resistance and vol_ratio>=1.25: return "BREAKOUT"
    if price>e20 and near_res and macd_rising: return "MOMENTUM"
    if price>e20 and (near_support or price<=e20*1.012): return "PULLBACK"
    if rsi<45 and macd_rising and near_support: return "REVERSAL"
    return "MOMENTUM"


def _audit_reject(audit, stage):
    if audit is not None:
        audit.reject(stage)
    return None


def intraday_signal(coin, interval="15m", limit=180, min_vol_x=None, min_hour_vol=0, chg24=None, min_potential_pct=5.0, max_potential_pct=200.0, min_score=None, min_rr=None, realtime_bars=None, cfg=None, audit=None):
    """Generate a strategy-specific scalp/small-trade setup with structure and liquidity confirmation."""
    profile = strategy_profile(interval)
    if not profile:
        return None
    try:
        effective_min_vol_x = profile["min_vol_x"] if min_vol_x is None else max(float(min_vol_x), profile["min_vol_x"])
        effective_min_score = profile["min_score"] if min_score is None else max(float(min_score), profile["min_score"])
        effective_min_rr = profile["min_rr"] if min_rr is None else max(float(min_rr), profile["min_rr"])
        data = _fetch_klines(coin, interval, limit)
        if not data:
            return _audit_reject(audit, "data_fetch")
        # Binance REST includes the currently forming candle. Never score an
        # unclosed candle; realtime_bars are already filtered to closed candles.
        data = data[:-1]
        if realtime_bars:
            # Replace only overlapping closed candles; keep REST history for warm-up.
            merged = {int(k[0]): k for k in data}
            for b in realtime_bars:
                merged[int(b["open_time"])] = [
                    b["open_time"], b["open"], b["high"], b["low"], b["close"],
                    b["volume"], 0, b["quote_volume"]
                ]
            data = [merged[k] for k in sorted(merged)][-limit:]
        closes = [float(k[4]) for k in data]
        highs = [float(k[2]) for k in data]
        lows = [float(k[3]) for k in data]
        vols = [float(k[5]) for k in data]
        qvols = [float(k[7]) for k in data]
        if len(closes) < 70 or (min_hour_vol and sum(qvols[-4:]) < min_hour_vol):
            return _audit_reject(audit, "liquidity")

        e9 = ind.ema(closes, 9)
        e20 = ind.ema(closes, 20)
        e21 = ind.ema(closes, 21)
        m, sig, hist = ind.macd(closes)
        r = ind.rsi(closes, 14) or 50
        a = ind.atr(highs, lows, closes) or closes[-1] * 0.01
        price = closes[-1]
        recent_vol = sum(vols[-2:]) / 2
        base_vol = sum(vols[-22:-2]) / 20
        vol_ratio = recent_vol / base_vol if base_vol > 0 else 0
        strategy_min_vol_x = profile["min_vol_x"]
        if vol_ratio < strategy_min_vol_x:
            return _audit_reject(audit, "volume")

        resistance = max(highs[-21:-1])
        support = min(lows[-21:-1])
        range_high = max(highs[-12:-1])
        macd_rising = hist[-1] > hist[-2]
        ema_bull = e9[-1] > e21[-1] and price > e20[-1]
        trend = _trend_filter(coin, interval)
        if not trend:
            return _audit_reject(audit, "trend_data")

        structure = structure_snapshot(closes, highs, lows)
        entry = evaluate_entry(
            closes, highs, lows, [float(k[1]) for k in data], interval, atr=a,
            require_retest=True,
            require_sweep=False,
        )
        if not entry:
            return _audit_reject(audit, "entry_confirmation")
        near_support = abs(price / support - 1) <= 0.012 if support else False
        setup = _setup_type(price, resistance, support, e20[-1], vol_ratio, macd_rising, r)
        if structure["choch"] and setup != "BREAKOUT":
            setup = "REVERSAL"
        elif structure["bos"] and price >= range_high and vol_ratio >= 1.15:
            setup = "BREAKOUT"

        if setup == "REVERSAL":
            structure_score = 14 if near_support else 5
        elif setup == "BREAKOUT":
            structure_score = 22 if price >= range_high else 12
        elif setup == "PULLBACK":
            structure_score = 18
        else:
            structure_score = 15

        score = 0.0
        reasons = []
        if ema_bull:
            score += 18
            reasons.append("EMA structure bullish")
        elif price > e20[-1]:
            score += 10
            reasons.append("price above EMA20")
        else:
            score -= 5
        if m[-1] > sig[-1]:
            score += 12
            reasons.append("MACD bullish")
        if macd_rising:
            score += 7
            reasons.append("MACD histogram rising")
        if 48 <= r <= 70:
            score += 10
            reasons.append("RSI healthy")
        elif 40 <= r < 48:
            score += 5
            reasons.append("RSI recovering")
        elif r > 78:
            score -= 8
            reasons.append("RSI extended")
        if vol_ratio >= 2:
            score += 15
            reasons.append(f"volume x{vol_ratio:.1f}")
        elif vol_ratio >= 1.5:
            score += 11
            reasons.append(f"volume x{vol_ratio:.1f}")
        else:
            score += 7
            reasons.append(f"volume x{vol_ratio:.1f}")

        score += structure_score + trend["score"]
        score += min(18.0, structure["score"] * 0.25)
        if structure["higher_lows"]:
            reasons.append("higher lows")
        if structure["bos"]:
            reasons.append("BOS")
        if structure["choch"]:
            reasons.append("CHOCH")
        if structure["liquidity_sweep"]:
            reasons.append("liquidity sweep + reclaim")
        if structure["compression"] > 0.15:
            reasons.append("compression before expansion")
        reasons.append(f"{trend['interval']} {trend['state'].lower()}")
        score = max(0, min(100, score))

        stop_dist = max(profile["stop_atr"] * a, price * 0.006)
        stop = price - stop_dist
        risk_pct = _pct(price, stop)
        structure_target = resistance if resistance > price else range_high
        volatility_target = price + profile["target_atr"] * a
        target = max(structure_target, volatility_target)
        potential = _pct(target, price)
        if potential < min_potential_pct:
            return _audit_reject(audit, "target_potential")
        potential = min(float(max_potential_pct), potential)
        target = price * (1 + potential / 100)
        rr = potential / risk_pct if risk_pct > 0 else 0
        if trend["state"] == "BEARISH" and setup != "REVERSAL":
            return _audit_reject(audit, "bearish_trend")

        expansion = classify_expansion(closes, highs, lows, vols, a)
        entry_quality = float(entry["entry_quality"])
        if entry["extension_pct"] > 4.0:
            score -= min(10.0, entry["extension_pct"] * 1.5)
        score = max(0.0, min(100.0, score))
        expansion_score = max(0.0, min(100.0, score + min(15.0, max(0.0, potential - 15.0))))
        # Prefer early expansion without accepting a late pump merely because
        # its absolute potential is large.
        expansion_score = round(
            max(0.0, min(100.0, expansion_score * 0.70 + expansion["score"] * 0.30)), 1
        )
        if expansion["state"] == "LATE_EXTENSION" and setup != "REVERSAL":
            return _audit_reject(audit, "late_extension")
        if expansion["state"] == "EARLY_EXPANSION":
            reasons.append("early expansion")
        elif expansion["state"] == "EXPANSION":
            reasons.append("expansion confirmed")

        provisional = {"coin": coin, "interval": interval, "setup_type": setup}
        outcome_stats = signal_history.comparable_stats(provisional, min_samples=20)
        adaptive_cfg = cfg or {}
        adaptive = adaptive_thresholds(
            interval, setup, effective_min_score, effective_min_vol_x,
            effective_min_rr, outcome_stats, adaptive_cfg,
        )
        if adaptive_cfg.get("adaptive_thresholds_enabled", True) and (score < adaptive["min_score"] or vol_ratio < adaptive["min_vol_x"] or rr < adaptive["min_rr"]):
            return _audit_reject(audit, "adaptive_thresholds")
        reasons.append(
            f"thresholds {adaptive['mode'].lower()}"
            + (f" ({adaptive['sample']} outcomes)" if adaptive["sample"] else "")
        )

        # Stage the three targets across the modelled move. Mature live
        # TP reach evidence may make a bounded adjustment, but the technical
        # target, R:R, and 5-80% potential envelope remain hard constraints.
        target_evidence = signal_history.staged_target_stats(
            min_samples=int((cfg or {}).get("target_min_samples", 30) or 30)
        )
        target_plan = optimize_targets(
            {
                "entry": price,
                "t1": price + (target - price) * 0.35,
                "t2": price + (target - price) * 0.65,
                "t3": target,
                "target": target,
                "risk_pct": risk_pct,
                "rr": effective_min_rr,
                "interval": interval,
                "setup_type": setup,
                "min_potential_pct": min_potential_pct,
                "max_potential_pct": max_potential_pct,
            },
            target_evidence,
            min_samples=int((cfg or {}).get("target_min_samples", 30) or 30),
            enabled=bool((cfg or {}).get("target_optimization_enabled", True)),
        )
        t1, t2, target = target_plan["t1"], target_plan["t2"], target_plan["t3"]
        potential = _pct(target, price)
        rr = potential / risk_pct if risk_pct > 0 else 0
        if rr < effective_min_rr or potential < min_potential_pct or potential > max_potential_pct:
            return _audit_reject(audit, "target_quality")
        if target_plan["mode"] != "BASE":
            reasons.append(
                f"targets {target_plan['mode'].lower()} "
                f"({target_plan['evidence_sample']} live outcomes)"
            )

        result = {
            "coin": coin, "interval": interval, "strategy": profile["kind"],
            "trade_horizon": profile["kind"],
            "estimated_hold_min_hours": profile.get("hold_min_hours"),
            "estimated_hold_max_hours": profile.get("hold_max_hours"),
            "candle_open_time": int(data[-1][0]),
            "strategy_id": next(k for k, v in STRATEGY_PROFILES.items() if v is profile),
            "price": price, "entry": price, "stop": stop,
            "t1": t1, "t2": t2, "t3": target, "target": target,
            "rsi": round(r, 1), "vol_x": round(vol_ratio, 2), "chg24": round(float(chg24 or 0), 2),
            "st": round(score, 1), "score": round(score, 1), "potential_pct": round(potential, 1),
            "risk_pct": round(risk_pct, 2), "rr": round(rr, 2), "atr": round(a, 6),
            "resistance": resistance, "support": support, "setup_type": setup, "trend_4h": trend["state"], "trend_interval": trend["interval"],
            "e9_e21": e9[-1] > e21[-1], "macd_rising": macd_rising, "reasons": reasons,
            "entry_quality": round(entry_quality, 1), "expansion_score": round(expansion_score, 1),
            "target_quality_mode": target_plan["mode"],
            "target_quality_adjustment_pct": target_plan["adjustment_pct"],
            "target_quality_evidence_scope": target_plan["evidence_scope"],
            "target_quality_evidence_sample": target_plan["evidence_sample"],
            "expansion_state": expansion["state"], "expansion_volume_ratio": expansion["volume_ratio"],
            "expansion_range_ratio": expansion["expansion"], "expansion_extension_pct": expansion["extension_pct"],
            "entry_trigger": entry["entry_trigger"], "liquidity_sweep_confirmed": entry["liquidity_sweep_confirmed"],
            "reclaim_confirmed": entry["reclaim_confirmed"], "bos_confirmed": entry["bos_confirmed"],
            "retest_confirmed": entry["retest_confirmed"], "confirmation_candle": entry["confirmation_candle"],
            "bos_level": entry["bos_level"], "sweep_level": entry["sweep_level"],
            "retest_level": entry["retest_level"], "retest_distance_pct": entry["retest_distance_pct"],
            "entry_extension_pct": entry["extension_pct"], "confirmation_body": entry["confirmation_body"],
            "structure_score": round(structure["score"], 1), "bos": structure["bos"],
            "choch": structure["choch"], "liquidity_sweep": structure["liquidity_sweep"],
            "higher_lows": structure["higher_lows"], "compression": structure["compression"],
            "component_flags": {
                "compression": float(structure.get("compression", 0.0)) >= 0.15,
                "liquidity_sweep": bool(entry.get("liquidity_sweep_confirmed")),
                "reclaim": bool(entry.get("reclaim_confirmed")),
                "bos": bool(entry.get("bos_confirmed")),
                "retest": bool(entry.get("retest_confirmed")),
                "volume_acceleration": float(expansion.get("volume_ratio", 0.0)) >= 1.5,
                "early_expansion": expansion["state"] == "EARLY_EXPANSION",
                "expansion": expansion["state"] in ("EARLY_EXPANSION", "EXPANSION"),
            },
        }
        stats = outcome_stats
        result["historical_win_pct"] = stats["win_pct"]
        result["historical_sample"] = stats["sample"]
        result["historical_wins"] = stats["wins"]
        result["historical_losses"] = stats["losses"]
        result["historical_scope"] = stats["scope"]
        result["historical_avg_mfe_pct"] = stats.get("avg_mfe_pct", 0.0)
        result["historical_avg_mae_pct"] = stats.get("avg_mae_pct", 0.0)
        result["historical_milestone_rates"] = stats.get("milestone_rates", {})
        if stats["win_pct"] is not None:
            scope = "exact setup" if stats["scope"] == "exact" else "setup/timeframe"
            reasons.append(f"historical {stats['win_pct']:.0f}% ({stats['sample']} {scope} results)")
        signal_history.record_signal(result)
        if audit is not None:
            audit.accept("qualified")
        return result
    except Exception:
        if audit is not None:
            audit.reject("exception")
        return None
