"""Auto Breakout Toolkit (ABT) BUY strategy.

Native, causal translation of the TradingView Auto Breakout Toolkit:
- ABT~ = shakeout + absorption (earliest)
- ABT* = demand-zone reversal
- ABT  = descending-trendline breakout

All pivot structures are delayed by pivot_right bars, so no future candle is
used at the decision candle. This module is deliberately independent from the
legacy BUY rules and is feature-gated by the caller.
"""
from __future__ import annotations

from statistics import mean
import indicators as ind
from target_quality import optimize_targets


ABT_STAGES = ("ABT~", "ABT*", "ABT")
ABT_NON_CRYPTO_ASSETS = {"EUR", "GBP", "AUD", "CAD", "CHF", "TRY", "BRL", "PLN", "UAH", "ZAR", "RUB"}


def _confidence_components(stage, mtf, vol_ratio, rsi, order_block, volatility, potential, rr):
    """Transparent ABT confidence model."""
    states = mtf.get("states", {})
    structure = {"ABT~": 20.0, "ABT*": 25.0, "ABT": 30.0}[stage]
    mtf_score = 0.0
    if states.get("4h") == "BULLISH": mtf_score += 15.0
    elif states.get("4h") == "MIXED": mtf_score += 7.0
    if states.get("1h") == "BULLISH": mtf_score += 6.0
    if states.get("1d") == "BULLISH": mtf_score += 4.0
    if states.get("1w") == "BULLISH": mtf_score += 3.0
    volume_score = min(15.0, max(0.0, float(vol_ratio)) * 7.5)
    momentum_score = 10.0 if 48.0 <= float(rsi) <= 68.0 else 7.0 if 40.0 <= float(rsi) <= 75.0 else 3.0
    ob_score = min(10.0, max(0.0, float((order_block or {}).get("quality_score", 0.0))) * 0.10)
    volatility_score = 5.0 if (volatility or {}).get("state") == "EXPANDING" else 3.0 if (volatility or {}).get("state") == "NORMAL" else 1.0
    liquidity_score = 5.0 if float(vol_ratio) >= 1.2 else 3.0 if float(vol_ratio) >= 0.8 else 1.0
    regime_score = 5.0 if states.get("1d") == "BULLISH" else 3.0 if states.get("1d") == "MIXED" else 1.0
    if states.get("1w") == "BEARISH":
        regime_score = max(0.0, regime_score - 1.0)
    total = structure + mtf_score + volume_score + momentum_score + ob_score + volatility_score + liquidity_score + regime_score
    return {"structure": round(structure,1), "mtf": round(mtf_score,1), "volume": round(volume_score,1), "momentum": round(momentum_score,1), "order_block": round(ob_score,1), "volatility": round(volatility_score,1), "liquidity": round(liquidity_score,1), "regime": round(regime_score,1), "total": round(min(100.0,total),1), "potential_pct": round(float(potential),2), "rr": round(float(rr),2)}


def _pivot_highs(highs, left=5, right=5, upto=None):
    end = len(highs) if upto is None else min(len(highs), upto + 1)
    out = []
    # A pivot at p is only knowable at p+right. Never emit a pivot whose
    # confirmation candle is after the decision candle.
    for p in range(left, max(left, end - right)):
        if p + right >= end:
            break
        v = highs[p]
        if all(v > highs[j] for j in range(p - left, p)) and all(v >= highs[j] for j in range(p + 1, p + right + 1)):
            out.append((p, v))
    return out


def _pivot_lows(lows, left=5, right=5, upto=None):
    end = len(lows) if upto is None else min(len(lows), upto + 1)
    out = []
    for p in range(left, max(left, end - right)):
        if p + right >= end:
            break
        v = lows[p]
        if all(v < lows[j] for j in range(p - left, p)) and all(v <= lows[j] for j in range(p + 1, p + right + 1)):
            out.append((p, v))
    return out


def _line_value(a, b, x):
    (i1, y1), (i2, y2) = a, b
    if i2 == i1:
        return y2
    return y1 + (y2 - y1) * ((x - i1) / (i2 - i1))


def _descending_trendline(highs, left=5, right=5, require_untouched=True):
    pivots = _pivot_highs(highs, left, right)
    if len(pivots) < 2:
        return None
    older, newer = pivots[-2], pivots[-1]
    if newer[1] >= older[1]:
        return None
    if require_untouched:
        slope = (newer[1] - older[1]) / (newer[0] - older[0])
        for i in range(older[0] + 1, newer[0]):
            line = older[1] + slope * (i - older[0])
            # A high through the line is a touch/violation; the pivot itself
            # is excluded. This mirrors the optional Pine untouched-line rule.
            if highs[i] >= line:
                return None
    return {
        "older_index": older[0], "older_price": older[1],
        "newer_index": newer[0], "newer_price": newer[1],
        "slope": (newer[1] - older[1]) / (newer[0] - older[0]),
    }


def _demand_zone(lows, atr, left=5, right=5):
    pivots = _pivot_lows(lows, left, right)
    if not pivots or atr <= 0:
        return None
    _, pivot_low = pivots[-1]
    bottom = pivot_low
    top = pivot_low + atr * 0.5
    return {"bottom": bottom, "top": top, "pivot_low": pivot_low}


def _zone_touched(lows, highs, zone, lookback=15):
    if not zone:
        return False, None
    start = max(0, len(lows) - lookback)
    for i in range(start, len(lows)):
        if lows[i] <= zone["top"] and highs[i] >= zone["bottom"]:
            return True, i
    return False, None


def _bullish_candle(opens, closes, lows, highs, i=-1):
    rng = max(0.0, highs[i] - lows[i])
    return closes[i] > opens[i] and rng > 0


def _mtf_state(closes):
    if len(closes) < 55:
        return "UNKNOWN"
    e20, e50 = ind.ema(closes, 20)[-1], ind.ema(closes, 50)[-1]
    spread = (e20 / e50 - 1.0) * 100 if e50 else 0.0
    if closes[-1] > e20 > e50 and spread >= 0.25:
        return "BULLISH"
    if closes[-1] < e20 < e50 and spread <= -0.25:
        return "BEARISH"
    return "MIXED"


def _mtf_alignment(interval, local_closes, mtf_closes=None):
    """Return causal multi-timeframe state for all supported spot intervals."""
    mtf_closes = mtf_closes or {}
    states = {k: _mtf_state(mtf_closes.get(k, []))
              for k in ("5m", "15m", "1h", "4h", "1d", "1w")}
    states[interval] = _mtf_state(local_closes)
    bullish = sum(v == "BULLISH" for v in states.values())
    bearish = sum(v == "BEARISH" for v in states.values())
    if bullish >= 2 and bullish > bearish:
        alignment = "BULLISH"
    elif bearish >= 2 and bearish > bullish:
        alignment = "BEARISH"
    else:
        alignment = "MIXED"
    return {"alignment": alignment, "states": states}


def _base_targets(price, highs, atr, max_potential=80.0, min_potential=5.0):
    """Return a real resistance target; never manufacture the configured floor.

    If there is no confirmed resistance beyond the minimum required potential,
    return 0 so the caller can reject the setup rather than presenting an
    artificial +5% target as if it were structural.
    """
    # Only use confirmed historical pivot highs as resistance. Raw candle
    # highs are too noisy and can turn an insignificant wick into a target.
    historical = list(highs[-120:-1])
    pivot_levels = [
        float(v) for _, v in _pivot_highs(historical, left=2, right=2)
        if float(v) > price
    ]
    resistances = sorted(set(pivot_levels))
    minimum_target = price * (1.0 + float(min_potential) / 100.0)
    structural = next((h for h in resistances if h >= minimum_target), 0.0)
    if structural <= price:
        return 0.0
    max_target = price * (1.0 + float(max_potential) / 100.0)
    return min(structural, max_target)


def _score(stage, candle_bull, zone_touch, trendline_break, shakeout, absorption,
           mtf, order_block=None, volatility=None):
    score = 40.0
    score += 18.0 if stage == "ABT" and trendline_break else 0.0
    score += 12.0 if stage == "ABT*" and zone_touch else 0.0
    score += 18.0 if stage == "ABT~" and shakeout else 0.0
    score += 12.0 if stage == "ABT~" and absorption else 0.0
    score += 8.0 if candle_bull else 0.0
    score += 7.0 if mtf.get("alignment") == "BULLISH" else -4.0 if mtf.get("alignment") == "BEARISH" else 0.0
    if order_block:
        score += min(8.0, float(order_block.get("quality_score", 0.0)) * 0.08)
    if volatility and volatility.get("state") == "EXPANDING":
        score += 4.0
    elif volatility and volatility.get("state") == "CONTRACTING":
        score -= 2.0
    return round(max(0.0, min(100.0, score)), 1)


def evaluate_abt(closes, highs, lows, opens, volumes, *, interval="15m",
                 atr=None, higher_closes=None, mtf_closes=None, order_block=None,
                 volatility=None, chg24=0.0, cfg=None):
    """Evaluate the latest closed candle only and return one ABT stage."""
    cfg = cfg or {}
    n = len(closes)
    if n < max(70, int(cfg.get("abt_min_bars", 70))):
        return None
    a = float(atr or ind.atr(highs, lows, closes) or 0.0)
    if a <= 0:
        return None

    price = float(closes[-1])
    candle_bull = _bullish_candle(opens, closes, lows, highs)
    rsi = float(ind.rsi(closes, 14) or 50.0)
    recent_vol = sum(volumes[-2:]) / 2.0
    base_vol = sum(volumes[-22:-2]) / 20.0
    vol_ratio = recent_vol / base_vol if base_vol > 0 else 0.0
    zone = _demand_zone(lows, a, int(cfg.get("abt_pivot_left", 5)), int(cfg.get("abt_pivot_right", 5)))
    if zone and price < zone["bottom"]:
        zone = None

    touched, touch_index = _zone_touched(
        lows, highs, zone, int(cfg.get("abt_touch_lookback", 15))
    )
    trendline = _descending_trendline(
        highs,
        int(cfg.get("abt_pivot_left", 5)),
        int(cfg.get("abt_pivot_right", 5)),
        bool(cfg.get("abt_require_untouched_trendline", True)),
    )
    line = _line_value(
        (trendline["older_index"], trendline["older_price"]),
        (trendline["newer_index"], trendline["newer_price"]),
        n - 1,
    ) if trendline else None
    buffer = a * float(cfg.get("abt_breakout_buffer_atr", 0.15))
    crossed = bool(trendline and price > line + buffer and closes[-2] <= (
        _line_value(
            (trendline["older_index"], trendline["older_price"]),
            (trendline["newer_index"], trendline["newer_price"]),
            n - 2,
        ) + buffer
    ))

    # Reversal BUY: zone touch + bullish body + lower wick >= 50% of range.
    rng = max(0.0, highs[-1] - lows[-1])
    lower_wick = min(opens[-1], closes[-1]) - lows[-1]
    reversal = bool(
        zone and touched and candle_bull and rng > 0 and lower_wick >= rng * 0.50
    )

    # Shakeout/absorption: bearish high-volume climax followed 1-3 bars later
    # by a quiet, narrow candle that holds above the climax low.
    climax = None
    climax_avg20 = 0.0
    for back in range(1, 4):
        i = n - 1 - back
        if i < 20:
            continue
        avg20 = mean(volumes[i - 20:i])
        if (
            closes[i] < opens[i]
            and volumes[i] >= avg20 * float(cfg.get("abt_climax_volume_x", 2.0))
            and zone and lows[i] <= zone["top"]
        ):
            climax = i
            climax_avg20 = avg20
            break
    absorption = False
    if climax is not None:
        quiet_range = highs[-1] - lows[-1]
        absorption = bool(
            volumes[-1] <= climax_avg20 * float(cfg.get("abt_absorption_volume_x", 0.60))
            and quiet_range <= (highs[climax] - lows[climax]) * float(cfg.get("abt_absorption_range_ratio", 0.40))
            and lows[-1] > lows[climax]
            and candle_bull
        )

    if mtf_closes is None and higher_closes is not None:
        mtf_closes = {"4h": higher_closes}
    mtf = _mtf_alignment(interval, closes, mtf_closes)
    if absorption:
        stage = "ABT~"
        setup = "SHAKEOUT_ABSORPTION"
    elif reversal:
        stage = "ABT*"
        setup = "DEMAND_REVERSAL"
    elif crossed:
        stage = "ABT"
        setup = "TRENDLINE_BREAKOUT"
    else:
        return None

    # ABT~ and ABT* are early discovery stages; ABT is the strongest
    # confirmation. Do not silently promote an early stage to legacy BUY.
    score = _score(stage, candle_bull, touched, crossed, climax is not None, absorption, mtf, order_block, volatility)
    min_score = float(cfg.get({
        "ABT~": "abt_min_score_early",
        "ABT*": "abt_min_score_reversal",
        "ABT": "abt_min_score_breakout",
    }[stage], 55.0))
    if score < min_score:
        return None

    # Higher-timeframe regime is a hard gate for actionable ABT signals.
    states = mtf["states"]
    if states.get("4h") == "BEARISH":
        return None
    if stage == "ABT" and not (states.get("4h") == "BULLISH" and states.get("1h") == "BULLISH"):
        return None
    if stage == "ABT" and states.get("1d") == "BEARISH":
        return None
    if stage == "ABT*" and states.get("4h") == "MIXED" and states.get("1h") != "BULLISH":
        return None

    if stage == "ABT*" and vol_ratio < float(cfg.get("abt_reversal_min_vol_x", 0.80)):
        strong_rejection = bool(rng > 0 and lower_wick >= rng * 0.65 and candle_bull)
        if not strong_rejection:
            return None
    if stage == "ABT" and vol_ratio < float(cfg.get("abt_breakout_min_vol_x", 1.20)):
        return None

    # Avoid chasing already-parabolic moves. The ABT setup remains visible only
    # after a fresh structural reset rather than treating a vertical 24h move as
    # a normal breakout/reversal entry.
    max_chg24 = float(cfg.get("abt_max_chg24_pct", 50.0))
    if max_chg24 > 0 and abs(float(chg24 or 0.0)) > max_chg24:
        return None

    max_potential = min(80.0, float(cfg.get("signal_max_potential_pct", 80.0)))
    min_potential = float(cfg.get("signal_min_potential_pct", 5.0))
    base_target = _base_targets(price, highs, a, max_potential, min_potential)
    if base_target <= price:
        return None
    # Build TP1/TP2 from real resistance levels when available. The prior
    # 35%/65% interpolation made every signal look artificially uniform even
    # when the market had meaningful nearby structure.
    resistance_levels = sorted({
        round(float(h), 12)
        for h in highs[-120:-1]
        if price < float(h) < base_target
    })
    ladder = resistance_levels[-2:] + [base_target]
    ladder = sorted(set(ladder))
    if len(ladder) >= 3:
        base_t1, base_t2 = ladder[-3], ladder[-2]
    elif len(ladder) == 2:
        base_t1, base_t2 = ladder[0], ladder[1]
    else:
        base_t1 = price + (base_target - price) * 0.35
        base_t2 = price + (base_target - price) * 0.65
    # Keep strict ordering even when clustered resistance levels are present.
    if not (price < base_t1 < base_t2 < base_target):
        base_t1 = price + (base_target - price) * 0.35
        base_t2 = price + (base_target - price) * 0.65
    target_plan = optimize_targets(
        {
            "entry": price,
            "t1": base_t1,
            "t2": base_t2,
            "t3": base_target,
            "target": base_target,
            "risk_pct": 0.0,
            "rr": float(cfg.get("signal_min_rr", 1.5)),
            "interval": interval,
            "setup_type": setup,
            "min_potential_pct": float(cfg.get("signal_min_potential_pct", 5.0)),
            "max_potential_pct": max_potential,
        },
        {},
        min_samples=int(cfg.get("target_min_samples", 30) or 30),
        enabled=bool(cfg.get("target_optimization_enabled", True)) and bool(cfg.get("abt_target_optimization_enabled", False)),
    )
    t1, t2, t3 = target_plan["t1"], target_plan["t2"], target_plan["t3"]
    potential = (t3 / price - 1.0) * 100 if price else 0.0
    risk_dist = max(a * float(cfg.get("abt_stop_atr", 1.5)), price * 0.006)
    stop = price - risk_dist
    risk_pct = (price / stop - 1.0) * 100 if stop > 0 else 0.0
    rr = potential / risk_pct if risk_pct else 0.0
    if potential < float(cfg.get("signal_min_potential_pct", 5.0)) or rr < float(cfg.get("signal_min_rr", 1.5)):
        return None

    confidence = _confidence_components(stage, mtf, vol_ratio, rsi, order_block, volatility, potential, rr)
    # Estimate a bounded holding window from target distance versus ATR.
    interval_minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}.get(interval, 60)
    atr_pct = (a / price) * 100.0 if price > 0 else 0.0
    bars_to_target = max(2.0, min(200.0, potential / max(atr_pct, 0.05) * 0.8))
    hold_min_hours = max(interval_minutes / 60.0, bars_to_target * interval_minutes / 60.0 * 0.5)
    hold_max_hours = max(hold_min_hours * 1.8, bars_to_target * interval_minutes / 60.0 * 2.0)
    reasons = {
        "ABT~": ["shakeout + absorption", "demand-zone hold"],
        "ABT*": ["demand-zone reversal", "lower-wick rejection"],
        "ABT": ["descending trendline breakout", f"+{buffer / price * 100:.2f}% ATR buffer"],
    }[stage]
    if mtf["alignment"] == "BULLISH":
        reasons.append("MTF bullish alignment")
    if order_block and order_block.get("bullish"):
        reasons.append("bullish order block")
    if volatility and volatility.get("state") == "EXPANDING":
        reasons.append("volatility expanding")

    return {
        "coin": "",
        "interval": interval,
        "strategy": "ABT",
        "strategy_family": stage,
        "strategy_id": f"auto_breakout_toolkit:{stage}",
        "trade_horizon": "Scalp" if interval in ("5m", "15m") else "Medium" if interval in ("1h", "4h") else "Long",
        "setup_type": setup,
        "entry_trigger": stage,
        "entry": price,
        "price": price,
        "stop": stop,
        "t1": t1, "t2": t2, "t3": t3, "target": t3,
        "potential_pct": round(potential, 1),
        "rsi": round(rsi, 1),
        "vol_x": round(vol_ratio, 2),
        "chg24": round(float(chg24 or 0.0), 2),
        "risk_pct": round(risk_pct, 2),
        "rr": round(rr, 2),
        "score": confidence["total"],
        "st": confidence["total"],
        "confidence_score": confidence["total"],
        "confidence_components": confidence,
        "signal_action": "WATCH" if stage == "ABT~" else "BUY",
        "estimated_hold_min_hours": round(hold_min_hours, 1),
        "estimated_hold_max_hours": round(hold_max_hours, 1),
        "atr": a,
        "candle_open_time": int(cfg.get("_candle_open_time", 0) or 0),
        "trend_4h": mtf["states"].get("4h", "UNKNOWN"),
        "trend_interval": "4h",
        "mtf_5m": mtf["states"].get("5m", "UNKNOWN"),
        "mtf_15m": mtf["states"].get("15m", "UNKNOWN"),
        "mtf_1h": mtf["states"].get("1h", "UNKNOWN"),
        "mtf_4h": mtf["states"].get("4h", "UNKNOWN"),
        "mtf_1d": mtf["states"].get("1d", "UNKNOWN"),
        "mtf_1w": mtf["states"].get("1w", "UNKNOWN"),
        "abt": {
            "stage": stage,
            "pivot_left": int(cfg.get("abt_pivot_left", 5)),
            "pivot_right": int(cfg.get("abt_pivot_right", 5)),
            "breakout_buffer_atr": float(cfg.get("abt_breakout_buffer_atr", 0.15)),
            "touch_lookback": int(cfg.get("abt_touch_lookback", 15)),
            "demand_zone": zone,
            "trendline": trendline,
            "trendline_value": line,
            "trendline_crossed": crossed,
            "zone_touched": touched,
            "zone_touch_index": touch_index,
            "shakeout_index": climax,
            "absorption_confirmed": absorption,
            "mtf": mtf,
        },
        "reasons": reasons,
        "component_flags": {
            "abt_shakeout": climax is not None,
            "abt_absorption": absorption,
            "abt_demand_touch": touched,
            "abt_trendline_break": crossed,
        },
    }


def evaluate_abt_coin(coin, interval="15m", limit=180, cfg=None, chg24=0.0):
    """Fetch closed spot candles and evaluate the latest ABT setup."""
    from botutil import http_json
    cfg = dict(cfg or {})
    coin = str(coin or "").upper()
    if coin in ABT_NON_CRYPTO_ASSETS:
        return None
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={int(limit)}"
    data = http_json(url, timeout=15)
    if not data or len(data) < 71:
        return None
    # Binance includes the currently forming candle. Drop it before all
    # pivot/trendline calculations.
    data = data[:-1]
    closes = [float(k[4]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    opens = [float(k[1]) for k in data]
    volumes = [float(k[5]) for k in data]
    mtf_closes = {}
    # All MTF series exclude the currently forming candle.
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        if tf == interval:
            continue
        higher = http_json(
            f"https://data-api.binance.vision/api/v3/klines?symbol={coin}USDT&interval={tf}&limit=100",
            timeout=15,
        )
        if higher and len(higher) > 1:
            mtf_closes[tf] = [float(k[4]) for k in higher[:-1]]
    try:
        import signal_context
        a = ind.atr(highs, lows, closes) or closes[-1] * 0.01
        ctx = signal_context.build_signal_context(closes, highs, lows, volumes, a, closes[-1])
        cfg["_candle_open_time"] = int(data[-1][0])
        result = evaluate_abt(
            closes, highs, lows, opens, volumes, interval=interval, atr=a,
            higher_closes=mtf_closes.get("4h"),
            mtf_closes=mtf_closes,
            order_block=ctx.get("order_block"),
            volatility=ctx.get("volatility"),
            chg24=chg24,
            cfg=cfg,
        )
        if result:
            result["coin"] = coin.upper()
        return result
    except Exception:
        return None
