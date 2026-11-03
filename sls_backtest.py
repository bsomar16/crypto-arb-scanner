#!/usr/bin/env python3
"""Causal historical replay for the native SLS SPOT strategy.

Input JSON schema:
{
  "candles": {
    "BTC": {"5m": [{"t": 0, "o": ..., "h": ..., "l": ..., "c": ..., "v": ..., "q": ...}]},
    "ETH": {"5m": [...]}
  }
}
Candle timestamps are Binance open times in milliseconds. The replay only
exposes candles whose close time is <= the signal evaluation timestamp.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import sls_strategy

INTERVAL_MS = {
    "5m": 5 * 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000, "1M": 30 * 24 * 60 * 60_000,
}
DEFAULT_HORIZON = {"5m": 288, "15m": 96, "1h": 48, "4h": 42, "1d": 30, "1w": 12}


def _normalise_dataset(payload):
    raw = payload.get("candles", payload)
    data = {}
    for symbol, intervals in raw.items():
        data[str(symbol).upper()] = {}
        for interval, rows in intervals.items():
            cleaned = []
            for row in rows:
                item = dict(row)
                item["t"] = int(item["t"])
                for key in ("o", "h", "l", "c", "v", "q"):
                    item[key] = float(item[key])
                cleaned.append(item)
            cleaned.sort(key=lambda x: x["t"])
            if any(cleaned[i]["t"] >= cleaned[i + 1]["t"] for i in range(len(cleaned) - 1)):
                raise ValueError(f"duplicate/nonascending candle timestamps: {symbol} {interval}")
            data[str(symbol).upper()][interval] = cleaned
    return data


def _provider(data, asof_ms):
    def fetch(symbol, interval, limit=240):
        duration = INTERVAL_MS.get(interval)
        if duration is None:
            return None
        rows = data.get(str(symbol).upper(), {}).get(interval, [])
        closed = [r for r in rows if r["t"] + duration <= asof_ms]
        return closed[-limit:] if len(closed) >= 200 else None
    return fetch


def _evaluate_outcome(signal, rows, index, horizon, fee_rate, slippage_rate):
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    targets = [float(signal[k]) for k in ("t1", "t2", "t3")]
    hits = [False, False, False]
    end = min(len(rows), index + 1 + max(1, horizon))
    future = rows[index + 1:end]
    if not future:
        return {"outcome": "CENSORED", "bars": 0, "hits": hits, "net_return_pct": None}
    exit_price = None
    outcome = None
    bars = 0
    for bars, candle in enumerate(future, 1):
        low, high = candle["l"], candle["h"]
        # Conservative intrabar rule: stop wins if stop and any target touch
        # in the same candle because their order cannot be inferred from OHLC.
        if low <= stop:
            outcome, exit_price = "STOP", stop
            break
        for i, target in enumerate(targets):
            if high >= target:
                hits[i] = True
        if high >= targets[-1]:
            outcome, exit_price = "TP3", targets[-1]
            break
    if outcome is None:
        if len(future) < max(1, horizon):
            outcome = "CENSORED"
            exit_price = None
        else:
            outcome = "EXPIRED"
            exit_price = future[-1]["c"]
    net = None
    if exit_price is not None:
        effective_entry = entry * (1.0 + fee_rate + slippage_rate)
        effective_exit = exit_price * (1.0 - fee_rate - slippage_rate)
        net = (effective_exit / effective_entry - 1.0) * 100.0
    return {"outcome": outcome, "bars": bars, "hits": hits,
            "net_return_pct": None if net is None else round(net, 5)}


def _stats(records):
    resolved = [r for r in records if r["outcome"] != "CENSORED"]
    n = len(records)
    scored = [r["net_return_pct"] for r in resolved if r["net_return_pct"] is not None]
    counts = {key: sum(r["outcome"] == key for r in records)
              for key in ("TP3", "STOP", "EXPIRED", "CENSORED")}
    denom = len(resolved)
    decisive = counts["TP3"] + counts["STOP"]
    return {
        "signals": n, **counts,
        "tp3_vs_stop_win_rate_pct": round(counts["TP3"] / decisive * 100, 2) if decisive else None,
        "stop_rate_pct": round(counts["STOP"] / decisive * 100, 2) if decisive else None,
        "tp1_hit_rate_pct": round(sum(r["hits"][0] for r in resolved) / denom * 100, 2) if denom else None,
        "tp2_hit_rate_pct": round(sum(r["hits"][1] for r in resolved) / denom * 100, 2) if denom else None,
        "tp3_hit_rate_pct": round(sum(r["hits"][2] for r in resolved) / denom * 100, 2) if denom else None,
        "resolved_net_return_avg_pct": round(sum(scored) / len(scored), 4) if scored else None,
        "resolved_net_return_sum_pct": round(sum(scored), 4) if scored else None,
        "resolved_samples": len(scored),
    }


def run_replay(dataset, sensitivity="Balanced", fee_rate=0.001, slippage_rate=0.0005,
               train_fraction=0.7, horizons=None):
    if not 0.5 <= train_fraction < 1.0:
        raise ValueError("train_fraction must be >= 0.5 and < 1.0")
    if fee_rate < 0 or slippage_rate < 0 or fee_rate + slippage_rate >= 0.1:
        raise ValueError("invalid fee/slippage assumptions")
    data = _normalise_dataset(dataset)
    cfg = {"sls_enabled": True, "sls_sensitivity": sensitivity}
    horizons = {**DEFAULT_HORIZON, **(horizons or {})}
    records = []
    for coin, by_interval in data.items():
        for interval in sls_strategy.INTERVALS:
            rows = by_interval.get(interval, [])
            duration = INTERVAL_MS[interval]
            if len(rows) < 201:
                continue
            for idx in range(199, len(rows) - 1):
                asof = rows[idx]["t"] + duration
                fetch = _provider(data, asof)
                signal = sls_strategy.evaluate_sls_coin(
                    coin, interval, cfg, candle_provider=fetch
                )
                if not signal or int(signal.get("candle_open_time", -1)) != rows[idx]["t"]:
                    continue
                # Pine's edge-trigger rule: only the first bar of a continuous
                # confirmed condition is eligible, not every qualifying bar.
                prev_asof = rows[idx - 1]["t"] + duration
                prev_signal = sls_strategy.evaluate_sls_coin(
                    coin, interval, cfg, candle_provider=_provider(data, prev_asof)
                )
                if prev_signal:
                    continue
                result = _evaluate_outcome(
                    signal, rows, idx, int(horizons[interval]), fee_rate, slippage_rate
                )
                records.append({
                    "coin": coin, "interval": interval, "timestamp": asof,
                    "entry": signal["entry"], "score": signal["score"],
                    "threshold": signal["sls_threshold"], **result,
                })

    grouped = defaultdict(list)
    for row in records:
        grouped[row["interval"]].append(row)
    report = {
        "method": "causal_asof_closed_candles",
        "sensitivity": sensitivity,
        "fees_per_side_pct": round(fee_rate * 100, 5),
        "slippage_per_side_pct": round(slippage_rate * 100, 5),
        "train_fraction": train_fraction,
        "by_interval": {},
        "signal_count": len(records),
    }
    for interval in sls_strategy.INTERVALS:
        items = sorted(grouped[interval], key=lambda x: x["timestamp"])
        cutoff_index = int(len(items) * train_fraction)
        train, test = items[:cutoff_index], items[cutoff_index:]
        report["by_interval"][interval] = {
            "all": _stats(items), "train": _stats(train), "out_of_sample": _stats(test),
            "oos_start_ms": test[0]["timestamp"] if test else None,
        }
    report["records"] = records
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="JSON candle dataset in documented schema")
    parser.add_argument("--output", default="sls_replay_report.json")
    parser.add_argument("--sensitivity", choices=("Conservative", "Balanced", "Aggressive"), default="Balanced")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="fraction per side; default 0.1%%")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="fraction per side; default 0.05%%")
    parser.add_argument("--train-fraction", type=float, default=0.7)
    args = parser.parse_args()
    payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    report = run_replay(payload, args.sensitivity, args.fee_rate, args.slippage_rate, args.train_fraction)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {k: {i: v["out_of_sample"] for i, v in report["by_interval"].items()}
               for k in ("by_interval",)}
    print(json.dumps({"signals": report["signal_count"], "oos": summary}, indent=2))


if __name__ == "__main__":
    main()
