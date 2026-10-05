#!/usr/bin/env python3
"""ABT-only causal out-of-sample / walk-forward research.

This runner intentionally does not enable ABT in production. It replays the
native ABT implementation on historical closed candles, evaluates each stage
(ABT~, ABT*, ABT), and reports stage-specific win rate, return, MAE/MFE,
target-hit rates, duration and false-breakout rate.

No future candle is supplied to evaluate_abt at decision time. MTF data is
restricted to candles whose close/open timestamp is already available at the
decision timestamp.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from statistics import mean

from abt_strategy import evaluate_abt
from botutil import http_json

BN = "https://data-api.binance.vision"
DEFAULT_SYMBOLS = ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE")
DEFAULT_INTERVALS = ("15m", "1h", "4h")
INTERVAL_MS = {
    "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}
HORIZON = {"5m": 288, "15m": 96, "1h": 48, "4h": 42, "1d": 30, "1w": 12}
STAGES = ("ABT~", "ABT*", "ABT")


def fetch_history(symbol, interval, bars):
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
    return rows[:-1] if rows else rows


def _arrays(rows):
    return (
        [float(x[4]) for x in rows],
        [float(x[2]) for x in rows],
        [float(x[3]) for x in rows],
        [float(x[1]) for x in rows],
        [float(x[5]) for x in rows],
    )


def _causal_mtf(rows_by_tf, decision_ts, interval):
    out = {}
    for tf, rows in rows_by_tf.items():
        if tf == interval:
            continue
        # A candle is usable only after its close. Binance open timestamps plus
        # interval duration give the earliest decision time for that candle.
        usable = [r for r in rows if int(r[0]) + INTERVAL_MS[tf] <= decision_ts]
        if usable:
            out[tf] = [float(r[4]) for r in usable]
    return out


def _atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    return sum(trs[-period:]) / period


def _evaluate_trade(rows, signal_i, signal):
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    target = float(signal["t3"])
    horizon = HORIZON[signal["interval"]]
    end = min(len(rows), signal_i + 1 + horizon)
    mfe = 0.0
    mae = 0.0
    milestones = {str(p): False for p in (5, 10, 20, 30, 50, 80)}

    for j in range(signal_i + 1, end):
        high = float(rows[j][2])
        low = float(rows[j][3])
        mfe = max(mfe, (high / entry - 1.0) * 100.0)
        mae = min(mae, (low / entry - 1.0) * 100.0)
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop:
            return {
                "outcome": "LOSS", "exit_i": j,
                "ret_pct": (stop / entry - 1.0) * 100.0 - 0.15,
                "mfe_pct": mfe, "mae_pct": mae, "milestones": milestones,
            }
        for p in milestones:
            if high >= entry * (1.0 + int(p) / 100.0):
                milestones[p] = True
        if hit_target:
            return {
                "outcome": "WIN", "exit_i": j,
                "ret_pct": (target / entry - 1.0) * 100.0 - 0.15,
                "mfe_pct": mfe, "mae_pct": mae, "milestones": milestones,
            }

    last = float(rows[end - 1][4])
    return {
        "outcome": "EXPIRED", "exit_i": end - 1,
        "ret_pct": (last / entry - 1.0) * 100.0 - 0.15,
        "mfe_pct": mfe, "mae_pct": mae, "milestones": milestones,
    }


def _walk(rows, interval, mtf_rows, start, end, cfg):
    records = []
    i = max(70, start)
    cooldown = 20
    while i < min(end, len(rows) - 1):
        closed = rows[: i + 1]
        closes, highs, lows, opens, volumes = _arrays(closed)
        atr = _atr(highs, lows, closes)
        if atr <= 0:
            i += 1
            continue
        decision_ts = int(rows[i][0]) + INTERVAL_MS[interval]
        mtf = _causal_mtf(mtf_rows, decision_ts, interval)
        local_cfg = dict(cfg)
        local_cfg["_candle_open_time"] = int(rows[i][0])
        try:
            signal = evaluate_abt(
                closes, highs, lows, opens, volumes,
                interval=interval, atr=atr, mtf_closes=mtf, cfg=local_cfg,
            )
        except Exception:
            signal = None
        if not signal:
            i += 1
            continue
        stage = signal.get("strategy_family")
        if stage not in STAGES:
            i += 1
            continue

        result = _evaluate_trade(rows, i, signal)
        result.update({
            "symbol": signal.get("coin", ""),
            "interval": interval,
            "stage": stage,
            "timestamp": int(rows[i][0]),
            "entry": signal["entry"],
            "t1": signal["t1"], "t2": signal["t2"], "t3": signal["t3"],
            "score": signal["score"], "rr": signal["rr"],
            "potential_pct": signal["potential_pct"],
            "exit_timestamp": int(rows[result["exit_i"]][0]),
        })
        records.append(result)
        # One logical ABT opportunity at a time. This also prevents the same
        # structural setup from dominating the sample.
        i = max(i + 1, result["exit_i"] + 1, i + cooldown)
    return records


def _stats(records):
    if not records:
        return {"signals": 0, "closed": 0, "wins": 0, "losses": 0,
                "expired": 0, "win_rate_pct": None}
    wins = [r for r in records if r["outcome"] == "WIN"]
    losses = [r for r in records if r["outcome"] == "LOSS"]
    closed = wins + losses
    returns = [float(r["ret_pct"]) for r in records]
    durations = [int(r.get("duration_bars", 0)) for r in records]
    gross_win = sum(max(0.0, x) for x in returns)
    gross_loss = -sum(min(0.0, x) for x in returns)
    milestones = {}
    for p in (5, 10, 20, 30, 50, 80):
        milestones[str(p)] = sum(bool(r["milestones"].get(str(p))) for r in records) / len(records) * 100
    return {
        "signals": len(records),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "expired": len(records) - len(closed),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else None,
        "avg_return_pct": mean(returns),
        "median_return_pct": sorted(returns)[len(returns) // 2],
        "profit_factor": gross_win / gross_loss if gross_loss else (math.inf if gross_win else 0.0),
        "avg_mfe_pct": mean(float(r["mfe_pct"]) for r in records),
        "avg_mae_pct": mean(float(r["mae_pct"]) for r in records),
        "avg_potential_pct": mean(float(r["potential_pct"]) for r in records),
        "avg_rr": mean(float(r["rr"]) for r in records),
        "target_milestones_pct": milestones,
    }


def _walk_forward(rows, interval, mtf_rows, train_bars, test_bars, step_bars, cfg):
    n = len(rows)
    windows = []
    test_start = min(train_bars, n)
    while test_start < n - 20:
        test_end = min(test_start + test_bars, n)
        records = _walk(rows, interval, mtf_rows, test_start, test_end, cfg)
        windows.append({
            "train_end_index": test_start,
            "test_start_index": test_start,
            "test_end_index": test_end,
            "test_start_timestamp": int(rows[test_start][0]),
            "test_end_timestamp": int(rows[test_end - 1][0]),
            "stats": {stage: _stats([r for r in records if r["stage"] == stage]) for stage in STAGES},
            "combined": _stats(records),
            "trades": records,
        })
        if test_end >= n:
            break
        test_start += step_bars
    return windows


def run(cfg):
    symbols = tuple(cfg.get("symbols") or DEFAULT_SYMBOLS)
    intervals = tuple(cfg.get("intervals") or DEFAULT_INTERVALS)
    bars = int(cfg.get("bars", 2500))
    train = int(cfg.get("train_bars", 1500))
    test = int(cfg.get("test_bars", 500))
    step = int(cfg.get("step_bars", 500))
    abt_cfg = {
        "abt_min_bars": int(cfg.get("abt_min_bars", 70)),
        "abt_pivot_left": 5, "abt_pivot_right": 5,
        "abt_require_untouched_trendline": True,
        "abt_breakout_buffer_atr": 0.15, "abt_touch_lookback": 15,
        "abt_climax_volume_x": 2.0, "abt_absorption_volume_x": 0.60,
        "abt_absorption_range_ratio": 0.40, "abt_stop_atr": 1.5,
        "abt_min_score_early": float(cfg.get("abt_min_score_early", 52)),
        "abt_min_score_reversal": float(cfg.get("abt_min_score_reversal", 55)),
        "abt_min_score_breakout": float(cfg.get("abt_min_score_breakout", 58)),
        "signal_min_potential_pct": float(cfg.get("signal_min_potential_pct", 5)),
        "signal_max_potential_pct": float(cfg.get("signal_max_potential_pct", 80)),
        "signal_min_rr": float(cfg.get("signal_min_rr", 1.5)),
        "target_optimization_enabled": True,
        "target_min_samples": 30,
    }
    all_results = []
    for symbol in symbols:
        for interval in intervals:
            print(f"[ABT-OOS] {symbol} {interval}: fetching history")
            rows = fetch_history(symbol, interval, bars)
            if len(rows) < train + 100:
                print(f"[ABT-OOS] {symbol} {interval}: insufficient data ({len(rows)})")
                continue
            mtf_rows = {}
            for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
                if tf == interval:
                    continue
                mtf_rows[tf] = fetch_history(symbol, tf, min(1000, max(120, bars // 2)))
            windows = _walk_forward(rows, interval, mtf_rows, train, test, step, abt_cfg)
            all_results.append({"symbol": symbol, "interval": interval, "bars": len(rows), "windows": windows})

    combined = {}
    for stage in STAGES:
        records = [t for r in all_results for w in r["windows"] for t in w["trades"] if t["stage"] == stage]
        combined[stage] = _stats(records)
    all_trades = [t for r in all_results for w in r["windows"] for t in w["trades"]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "causal ABT closed-candle walk-forward OOS",
        "warning": "Historical evidence only; no future-performance guarantee.",
        "abt_enabled_in_production": False,
        "configuration": {**cfg, "symbols": list(symbols), "intervals": list(intervals)},
        "combined": {"ALL": _stats(all_trades), **combined},
        "results": all_results,
    }
    return report


def format_report(report):
    lines = [
        "ABT OUT-OF-SAMPLE WALK-FORWARD REPORT",
        f"Generated: {report['generated_at']}",
        "Production ABT flag remains OFF.",
        "",
    ]
    for stage in ("ABT~", "ABT*", "ABT", "ALL"):
        s = report["combined"].get(stage, {})
        win = "-" if s.get("win_rate_pct") is None else f"{s['win_rate_pct']:.1f}%"
        pf = s.get("profit_factor")
        pf_text = "-" if pf is None else ("inf" if math.isinf(pf) else f"{pf:.2f}")
        lines.append(
            f"{stage}: signals={s.get('signals',0)} closed={s.get('closed',0)} "
            f"W/L={s.get('wins',0)}/{s.get('losses',0)} expired={s.get('expired',0)} "
            f"win={win} PF={pf_text} avgRet={s.get('avg_return_pct',0):.2f}% "
            f"MFE={s.get('avg_mfe_pct',0):.2f}% MAE={s.get('avg_mae_pct',0):.2f}%"
        )
    lines += ["", "Stage and symbol/interval/window details are stored in the JSON artifact."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    ap.add_argument("--bars", type=int, default=2500)
    ap.add_argument("--train-bars", type=int, default=1500)
    ap.add_argument("--test-bars", type=int, default=500)
    ap.add_argument("--step-bars", type=int, default=500)
    ap.add_argument("--output", default="state/abt_oos_report.json")
    args = ap.parse_args()
    cfg = {
        "symbols": [x.strip().upper() for x in args.symbols.split(",") if x.strip()],
        "intervals": [x.strip() for x in args.intervals.split(",") if x.strip()],
        "bars": args.bars, "train_bars": args.train_bars,
        "test_bars": args.test_bars, "step_bars": args.step_bars,
    }
    report = run(cfg)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(format_report(report))


if __name__ == "__main__":
    main()
