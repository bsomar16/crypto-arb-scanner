#!/usr/bin/env python3
"""Historical backtest for the crypto multi-timeframe signal engine.

The backtest evaluates closed candles only, then walks forward candle-by-candle
and records whether stop or target was reached first. If both are inside one
OHLC candle, the conservative assumption is that the stop was hit first.
"""

from datetime import datetime, timezone
from statistics import mean

import indicators as ind
from botutil import http_json

BN = "https://data-api.binance.vision"
INTERVALS = ("5m", "15m", "1h")
HORIZON_BARS = {"5m": 72, "15m": 48, "1h": 48}
STOP_MULT = {"5m": 1.25, "15m": 1.5, "1h": 1.8}
TARGET_MULT = {"5m": 3.0, "15m": 4.0, "1h": 5.0}
COST_PCT = 0.15


def _fetch_history(symbol, interval, bars=3000):
    """Fetch closed historical Binance candles with forward pagination."""
    rows = []
    end = None
    while len(rows) < bars:
        limit = min(1000, bars - len(rows))
        url = f"{BN}/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
        if end is not None:
            url += f"&endTime={end}"
        data = http_json(url, timeout=20)
        if not data:
            break
        batch = list(reversed(data))
        if rows:
            oldest = rows[0][0]
            batch = [x for x in batch if x[0] < oldest]
        if not batch:
            break
        rows = batch + rows
        end = int(batch[0][0]) - 1
        if len(data) < limit:
            break
    rows.sort(key=lambda x: x[0])
    # Exclude the currently forming candle.
    return rows[:-1] if rows else rows


def _trend_state(closes):
    if len(closes) < 55:
        return None, 0
    e20 = ind.ema(closes, 20)[-1]
    e50 = ind.ema(closes, 50)[-1]
    m, sig, _ = ind.macd(closes)
    if closes[-1] > e20 > e50 and m[-1] >= sig[-1]:
        return "BULLISH", 12
    if closes[-1] < e20 < e50 and m[-1] < sig[-1]:
        return "BEARISH", -10
    return "MIXED", 3


def _signal_at(rows, i, interval, min_vol_x=1.15, min_potential_pct=5.0,
               max_potential_pct=80.0, min_score=55.0, min_rr=1.5,
               trend_rows=None):
    """Recreate the live signal rules using data available at candle i only."""
    if i < 70:
        return None
    closed = rows[:i + 1]
    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    vols = [float(k[5]) for k in closed]
    if len(closes) < 70:
        return None

    e9 = ind.ema(closes, 9); e20 = ind.ema(closes, 20); e21 = ind.ema(closes, 21)
    macd, sig, hist = ind.macd(closes)
    rsi = ind.rsi(closes, 14) or 50.0
    atr = ind.atr(highs, lows, closes) or closes[-1] * 0.01
    price = closes[-1]
    recent_vol = sum(vols[-2:]) / 2
    base_vol = sum(vols[-22:-2]) / 20
    vol_x = recent_vol / base_vol if base_vol > 0 else 0
    if vol_x < min_vol_x:
        return None

    resistance = max(highs[-21:-1])
    support = min(lows[-21:-1])
    range_high = max(highs[-12:-1])
    macd_rising = hist[-1] > hist[-2]
    ema_bull = e9[-1] > e21[-1] and price > e20[-1]

    if trend_rows:
        ts = rows[i][0]
        eligible = [x for x in trend_rows if x[0] <= ts]
        if len(eligible) < 55:
            return None
        tcloses = [float(k[4]) for k in eligible]
        trend_state, trend_score = _trend_state(tcloses)
    else:
        trend_state, trend_score = "MIXED", 3

    near_res = resistance > 0 and abs(price / resistance - 1) <= 0.006
    near_support = support > 0 and abs(price / support - 1) <= 0.012
    if price > resistance and vol_x >= 1.25:
        setup = "BREAKOUT"
    elif price > e20[-1] and near_res and macd_rising:
        setup = "MOMENTUM"
    elif price > e20[-1] and (near_support or price <= e20[-1] * 1.012):
        setup = "PULLBACK"
    elif rsi < 45 and macd_rising and near_support:
        setup = "REVERSAL"
    else:
        setup = "MOMENTUM"

    if setup == "REVERSAL":
        structure_score = 14 if near_support else 5
    elif setup == "BREAKOUT":
        structure_score = 22 if price >= range_high else 12
    elif setup == "PULLBACK":
        structure_score = 18
    else:
        structure_score = 15

    score = 0.0
    if ema_bull:
        score += 18
    elif price > e20[-1]:
        score += 10
    else:
        score -= 5
    if macd[-1] > sig[-1]: score += 12
    if macd_rising: score += 7
    if 48 <= rsi <= 70: score += 10
    elif 40 <= rsi < 48: score += 5
    elif rsi > 78: score -= 8
    if vol_x >= 2: score += 15
    elif vol_x >= 1.5: score += 11
    else: score += 7
    score += structure_score + trend_score
    score = max(0.0, min(100.0, score))

    stop_dist = max(STOP_MULT[interval] * atr, price * 0.006)
    stop = price - stop_dist
    risk_pct = (price / stop - 1) * 100
    structure_target = resistance if resistance > price else range_high
    volatility_target = price + TARGET_MULT[interval] * atr
    target = max(structure_target, volatility_target)
    potential = (target / price - 1) * 100
    if potential < min_potential_pct:
        return None
    potential = min(max_potential_pct, potential)
    target = price * (1 + potential / 100)
    rr = potential / risk_pct if risk_pct > 0 else 0
    if score < min_score or rr < min_rr:
        return None
    if trend_state == "BEARISH" and setup != "REVERSAL":
        return None

    return {"timestamp": rows[i][0], "entry": price, "stop": stop,
            "target": target, "potential_pct": potential, "risk_pct": risk_pct,
            "rr": rr, "score": score, "setup_type": setup,
            "interval": interval, "trend_4h": trend_state, "rsi": rsi,
            "vol_x": vol_x}


def _evaluate(rows, signal_i, signal, horizon):
    end = min(len(rows), signal_i + 1 + horizon)
    stop = signal["stop"]
    target = signal["target"]
    entry = signal["entry"]
    for j in range(signal_i + 1, end):
        high = float(rows[j][2]); low = float(rows[j][3])
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            return {"outcome": "LOSS", "exit_i": j, "ret_pct": (stop / entry - 1) * 100 - COST_PCT}
        if hit_stop:
            return {"outcome": "LOSS", "exit_i": j, "ret_pct": (stop / entry - 1) * 100 - COST_PCT}
        if hit_target:
            return {"outcome": "WIN", "exit_i": j, "ret_pct": (target / entry - 1) * 100 - COST_PCT}
    last = float(rows[end - 1][4])
    return {"outcome": "EXPIRED", "exit_i": end - 1,
            "ret_pct": (last / entry - 1) * 100 - COST_PCT}


def run_symbol(symbol, interval="15m", bars=3000, warmup=100,
               min_vol_x=1.15, min_potential_pct=5.0,
               max_potential_pct=80.0, min_score=55.0, min_rr=1.5):
    rows = _fetch_history(symbol, interval, bars)
    trend_rows = _fetch_history(symbol, "4h", max(500, bars // 8))
    if len(rows) < warmup + 100:
        return None
    trades = []
    i = warmup
    while i < len(rows) - 2:
        signal = _signal_at(rows, i, interval, min_vol_x, min_potential_pct,
                            max_potential_pct, min_score, min_rr, trend_rows)
        if signal:
            result = _evaluate(rows, i, signal, HORIZON_BARS[interval])
            result.update(signal)
            trades.append(result)
            # Prevent overlapping positions; next setup starts after this one closes.
            i = max(i + 1, result["exit_i"] + 1)
        else:
            i += 1

    if not trades:
        return {"symbol": symbol, "interval": interval, "n": 0, "wins": 0,
                "losses": 0, "expired": 0, "win_pct": None, "avg_win": None,
                "avg_loss": None, "pf": None, "avg_potential": None,
                "avg_rr": None, "avg_hold_bars": None}

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    closed = wins + losses
    gross_wins = sum(max(0, t["ret_pct"]) for t in trades)
    gross_losses = -sum(min(0, t["ret_pct"]) for t in trades)
    return {
        "symbol": symbol, "interval": interval, "n": len(trades),
        "wins": len(wins), "losses": len(losses),
        "expired": len([t for t in trades if t["outcome"] == "EXPIRED"]),
        "win_pct": len(wins) / len(closed) * 100 if closed else None,
        "avg_win": mean([t["ret_pct"] for t in wins]) if wins else None,
        "avg_loss": mean([t["ret_pct"] for t in losses]) if losses else None,
        "pf": gross_wins / gross_losses if gross_losses else float("inf"),
        "avg_potential": mean([t["potential_pct"] for t in trades]),
        "avg_rr": mean([t["rr"] for t in trades]),
        "avg_hold_bars": mean([t["exit_i"] - next(i for i, r in enumerate(rows) if r[0] == t["timestamp"]) for t in trades]),
    }


def run_backtest(cfg):
    symbols = [str(x).upper() for x in (cfg.get("backtest_symbols", []) or ["BTC", "ETH", "SOL"])]
    intervals = tuple(x for x in cfg.get("backtest_intervals", INTERVALS) if x in INTERVALS) or INTERVALS
    bars = int(cfg.get("backtest_bars", 3000))
    kwargs = dict(min_vol_x=float(cfg.get("buy_min_vol_x", 1.15)),
                  min_potential_pct=float(cfg.get("signal_min_potential_pct", 5.0)),
                  max_potential_pct=float(cfg.get("signal_max_potential_pct", 80.0)),
                  min_score=float(cfg.get("signal_min_score", 55)),
                  min_rr=float(cfg.get("signal_min_rr", 1.5)))
    results = []
    for sym in symbols:
        for interval in intervals:
            try:
                r = run_symbol(sym, interval=interval, bars=bars, **kwargs)
                if r:
                    results.append(r)
                print(f"[BACKTEST] {sym} {interval}: done")
            except Exception as e:
                print(f"[BACKTEST] {sym} {interval}: error {e}")
    if not results:
        return "Backtest: no data."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📈 <b>MTF CRYPTO BACKTEST</b> · {now}",
             "Closed-candle walk-forward · no look-ahead · conservative same-candle SL/TP handling",
             f"Costs: {COST_PCT:.2f}% round trip · horizons: 5m={HORIZON_BARS['5m']} bars, 15m={HORIZON_BARS['15m']} bars, 1h={HORIZON_BARS['1h']} bars",
             ""]
    for r in results:
        if r["win_pct"] is None:
            win = "-"
        else:
            win = f"{r['win_pct']:.1f}%"
        pf = "∞" if r["pf"] == float("inf") else (f"{r['pf']:.2f}" if r["pf"] is not None else "-")
        avgp = f"{r['avg_potential']:.1f}%" if r["avg_potential"] is not None else "-"
        avgr = f"{r['avg_rr']:.2f}" if r["avg_rr"] is not None else "-"
        lines.append(f"<b>{r['symbol']} {r['interval']}</b> · setups {r['n']} · win {win} · W/L {r['wins']}/{r['losses']} · expired {r['expired']} · PF {pf} · avg potential {avgp} · avg R:R {avgr}")

    closed = [r for r in results if r["n"]]
    total_wins = sum(r["wins"] for r in closed)
    total_losses = sum(r["losses"] for r in closed)
    total_closed = total_wins + total_losses
    if total_closed:
        lines.extend(["", f"<b>Comparable setups: {total_closed}</b> · historical target-before-stop rate {total_wins / total_closed * 100:.1f}%"])
    lines.extend(["", "⚠️ Historical results are descriptive backtest statistics, not a guarantee of future performance."])
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_backtest({"backtest_symbols": ["BTC", "ETH", "SOL"], "backtest_intervals": ["15m", "1h"]}))
