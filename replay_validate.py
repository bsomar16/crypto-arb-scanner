#!/usr/bin/env python3
"""Resolve intrabar stop/target ordering with 1-minute klines.

The expectancy report simulates exits from a signal's window MFE/MAE, and when a
window contains BOTH the stop and the target it must guess the order. It guesses
**stop-first** (matching the outcome grader's conservative convention). That guess is
pessimistic, and it is also what biases the band optimiser toward the widest stop.

This script measures the size of that guess instead of trusting it: for every trade
whose window touched both levels, it fetches **1-minute** klines from the entry candle
and replays them in true sequence to see which level was actually reached first.

Outputs three expectancy numbers for the same policy:
  - worst_case : every ambiguous trade = stop   (the assumption currently in use)
  - resolved   : ambiguous trades scored by 1m ordering
  - best_case  : every ambiguous trade = target (upper bound)

If `resolved` sits near `worst_case`, the pessimistic assumption is harmless. If it
sits near `best_case`, the reported edge has been badly understated.

Read-only: caches to a JSON file, never touches bot state.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import backfill_outcomes as bf
import expectancy_report as er
import json

MINUTE = 60_000
# 1m windows are fetched per trade; skip horizons that would need absurd fetches.
MAX_MINUTES = 6 * 24 * 60


def load_signals_map(path: str) -> dict:
    return bf.load_signals(path)


def needed_minutes(interval: str, cfg: dict) -> int:
    step_min = bf.INTERVAL_MS.get(interval, 0) // MINUTE
    return step_min * bf.horizon_bars(interval, cfg)


def fetch_minute_window(symbol: str, start_ms: int, minutes: int, max_bars: int):
    """Fetch enough 1m bars from start_ms to cover `minutes`, paginating."""
    rows = []
    cursor = int(start_ms)
    target_bars = min(int(minutes), int(max_bars))
    while len(rows) < target_bars:
        chunk = bf.fetch_window(symbol, "1m", cursor, min(1000, target_bars - len(rows)))
        if not chunk:
            break
        rows.extend(chunk)
        cursor = int(chunk[-1][0]) + MINUTE
        if len(chunk) < 1000:
            break
    return rows[:target_bars]


def resolve(symbol: str, entry_ms: int, entry: float, target_pct: float, stop_pct: float,
            interval: str, cfg: dict, max_bars: int):
    """Return ('TARGET_FIRST'|'STOP_FIRST'|'SAME_BAR'|'NEITHER', bars)."""
    minutes = needed_minutes(interval, cfg)
    if minutes <= 0 or minutes > MAX_MINUTES:
        return "SKIPPED", 0
    rows = fetch_minute_window(symbol, entry_ms, minutes, max_bars)
    if not rows:
        return "NO_DATA", 0
    target_px = entry * (1.0 + target_pct / 100.0)
    stop_px = entry * (1.0 - stop_pct / 100.0)
    for i, bar in enumerate(rows, 1):
        try:
            high, low = float(bar[2]), float(bar[3])
        except (TypeError, ValueError):
            continue
        hit_stop = low <= stop_px
        hit_target = high >= target_px
        if hit_stop and hit_target:
            return "SAME_BAR", i          # 1m still cannot order it: treat as stop-first downstream
        if hit_stop:
            return "STOP_FIRST", i
        if hit_target:
            return "TARGET_FIRST", i
    return "NEITHER", 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resolve stop/target ordering with 1m klines")
    ap.add_argument("--history", default="state/signal_history.jsonl")
    ap.add_argument("--outcomes", default="state/signal_outcomes.json")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--target-pct", type=float, default=er.CONFIGURED_TARGET_PCT)
    ap.add_argument("--stop-pct", type=float, default=er.CONFIGURED_STOP_PCT)
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-bars", type=int, default=3000)
    ap.add_argument("--cache", default="state/order_validation_cache.json")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--outcomes-out", default=None,
                    help="write a copy of the outcomes file with ambiguous trades re-ordered per 1m data")
    args = ap.parse_args(argv)

    cfg = json.load(open(args.config, encoding="utf-8")) if os.path.exists(args.config) else {}
    cost = er.cost_pct(args.fee_bps, args.slippage_bps)
    rows = er.load_rows(args.history, args.outcomes)
    signals = load_signals_map(args.history)
    if not rows:
        print("error: no graded signals", flush=True)
        return 1

    t, s = abs(args.target_pct), abs(args.stop_pct)
    ambiguous = [r for r in rows if r["mfe"] >= t and r["mae"] <= -s]
    ambiguous.sort(key=lambda r: r["ts"])
    # skip horizons we cannot resolve with 1m data
    resolvable, skipped = [], 0
    for r in ambiguous:
        sig = signals.get(r["id"]) or {}
        iv = str(r.get("interval") or "")
        mins = needed_minutes(iv, cfg)
        if mins <= 0 or mins > MAX_MINUTES:
            skipped += 1
            continue
        resolvable.append((r, sig, iv, mins))
    resolvable = resolvable[: max(0, args.limit)]

    cache = bf.load_json(args.cache, {}) or {}
    todo = [(r, sig, iv, mins) for (r, sig, iv, mins) in resolvable if r["id"] not in cache]
    print(f"graded={len(rows)} ambiguous={len(ambiguous)} resolvable={len(resolvable)} "
          f"cached={len(resolvable) - len(todo)} skipped(1m horizon)={skipped}", flush=True)

    if todo:
        started = time.time()
        def work(item):
            r, sig, iv, _ = item
            entry = er._f(sig.get("entry"))
            entry_ms = int(sig.get("candle_open_time") or 0)
            if not entry or not entry_ms:
                return r["id"], {"verdict": "NO_DATA"}
            verdict, bars = resolve(str(sig.get("coin") or ""), entry_ms, entry, t, s,
                                    iv, cfg, args.max_bars)
            return r["id"], {"verdict": verdict, "bars_1m": bars}
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for sig_id, res in pool.map(work, todo):
                cache[sig_id] = res
        os.makedirs(os.path.dirname(args.cache) or ".", exist_ok=True)
        with open(args.cache, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
        print(f"resolved {len(todo)} in {time.time()-started:.1f}s (cache saved)", flush=True)

    counts = {}
    for r, sig, iv, _ in resolvable:
        v = (cache.get(r["id"]) or {}).get("verdict", "NO_DATA")
        counts[v] = counts.get(v, 0) + 1

    def expectancy(mode: str):
        pnls = []
        for r, sig, iv, _ in resolvable:
            v = (cache.get(r["id"]) or {}).get("verdict", "NO_DATA")
            if v == "TARGET_FIRST" and mode != "worst_case":
                pnls.append(t)
            elif v == "NEITHER":
                pnls.append(0.0)
            elif mode == "best_case" and v in ("STOP_FIRST", "SAME_BAR"):
                pnls.append(t)
            else:
                pnls.append(-s)
        return er.stats(pnls, cost), pnls

    out = {"ambiguous": len(ambiguous), "resolvable": len(resolvable), "skipped": skipped,
           "verdicts": counts, "cost_pct": round(cost, 3)}
    print("\nverdict distribution (1m ordering):")
    for k in sorted(counts, key=lambda x: -counts[x]):
        print(f"  {k:14s} {counts[k]:5d}")
    print()
    for mode in ("worst_case", "resolved", "best_case"):
        st, _ = expectancy(mode)
        out[mode] = st
        print(f"  {mode:11s} expectancy {st['expectancy_pct']:+.3f}%/trade  win {st['win_pct']}%  PF {st['profit_factor']}")

    if args.outcomes_out:
        fixed = json.load(open(args.outcomes, encoding="utf-8"))
        applied = 0
        for r in ambiguous:
            v = (cache.get(r["id"]) or {}).get("verdict")
            rec = fixed.get(r["id"])
            if not rec or v not in ("TARGET_FIRST", "STOP_FIRST", "SAME_BAR"):
                continue
            # nudge the excursions so the window simulator resolves the observed order
            if v == "TARGET_FIRST":
                rec["mae_pct"] = max(-s + 0.001, float(rec.get("mae_pct", -s)))
                rec["mfe_pct"] = max(t, float(rec.get("mfe_pct", t)))
            else:
                rec["mfe_pct"] = min(t - 0.001, float(rec.get("mfe_pct", t)))
                rec["mae_pct"] = min(-s, float(rec.get("mae_pct", -s)))
            rec["order_resolved"] = v
            applied += 1
        with open(args.outcomes_out, "w", encoding="utf-8") as fh:
            json.dump(fixed, fh, indent=1, sort_keys=True)
        print(f"\nwrote {args.outcomes_out} ({applied} trades re-ordered)")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
