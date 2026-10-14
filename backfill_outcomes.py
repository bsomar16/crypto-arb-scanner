#!/usr/bin/env python3
"""Backfill graded outcomes for already-logged BUY signals.

`signal_outcomes.update_pending()` can only grade signals whose entry candle is still
inside a "most recent N candles" window, so anything older than the horizon never gets
graded - which is why the outcome set stays tiny while signal_history keeps growing.

This script fetches klines anchored at each signal's own `candle_open_time`
(Binance `startTime`) and reuses `signal_outcomes._evaluate` unchanged, so backfilled
records use exactly the same grading rules (including the conservative stop-first
ordering) as live grading.

Safe by default: it writes to a separate file and never touches live state unless
`--write` is passed.

Usage:
    python backfill_outcomes.py --limit 200                 # dry run -> backfill file
    python backfill_outcomes.py --limit 200 --write         # merge into state/signal_outcomes.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import signal_outcomes as so

INTERVAL_MS = {
    "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
    "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}
HISTORY_DEFAULT = "state/signal_history.jsonl"
OUTCOMES_DEFAULT = "state/signal_outcomes.json"
BACKFILL_DEFAULT = "state/signal_outcomes_backfill.json"
CONFIG_DEFAULT = "config.json"


def now_ms() -> int:
    """Injectable clock (patched in tests instead of mutating the stdlib time module)."""
    return int(time.time() * 1000)


def load_signals(path: str) -> dict:
    out: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("kind") == "signal" and row.get("id"):
                out[row["id"]] = row
    return out


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def horizon_bars(interval: str, cfg: dict) -> int:
    override = (cfg.get("outcome_horizon_bars") or {}).get(interval)
    if override:
        return int(override)
    return int(so.DEFAULT_HORIZON_BARS.get(interval, 96))


def is_mature(sig: dict, interval: str, cfg: dict, now_ms: int) -> bool:
    """True only once the full grading horizon has closed after the entry candle."""
    entry = int(sig.get("candle_open_time") or 0)
    if not entry:
        return False
    step = INTERVAL_MS.get(interval)
    if not step:
        return False
    needed = entry + (horizon_bars(interval, cfg) + 2) * step
    return now_ms >= needed


def fetch_window(symbol: str, interval: str, start_ms: int, limit: int, retries: int = 3):
    """Fetch closed klines anchored at start_ms (Binance startTime)."""
    url = (f"{so.BINANCE}/api/v3/klines?symbol={symbol}USDT&interval={interval}"
           f"&startTime={int(start_ms)}&limit={int(limit)}")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-arb-scanner/1.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                rows = json.loads(response.read().decode("utf-8"))
            if not isinstance(rows, list):
                return []
            return [r for r in rows if len(r) >= 6]
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429) and attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            return []
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            return []
    return []


def grade_one(sig: dict, cfg: dict):
    interval = str(sig.get("interval") or "")
    limit = horizon_bars(interval, cfg) + 2
    rows = fetch_window(str(sig.get("coin") or ""), interval,
                        int(sig.get("candle_open_time") or 0), limit)
    if not rows:
        return None
    result = so._evaluate(sig, rows, horizon_bars(interval, cfg))
    if not result:
        return None
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    result.update({
        "coin": sig.get("coin"),
        "interval": interval,
        "setup_type": sig.get("setup_type"),
        "evaluated_at": now_iso,
        "updated_at": now_iso,
        "backfilled": True,
    })
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill graded outcomes for logged signals")
    ap.add_argument("--history", default=HISTORY_DEFAULT)
    ap.add_argument("--outcomes", default=OUTCOMES_DEFAULT)
    ap.add_argument("--config", default=CONFIG_DEFAULT)
    ap.add_argument("--out", default=BACKFILL_DEFAULT, help="merged output path (dry run)")
    ap.add_argument("--limit", type=int, default=200, help="max signals to grade this run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--write", action="store_true", help="merge into --outcomes in place")
    ap.add_argument("--interval-filter", default="", help="only this interval, e.g. 1h")
    args = ap.parse_args(argv)

    cfg = load_json(args.config, {}) or {}
    signals = load_signals(args.history)
    graded = load_json(args.outcomes, {}) or {}
    if not isinstance(graded, dict):
        graded = {}
    # Resume support: accumulate on top of any previous dry-run output so the
    # backlog can be graded in chunks without losing earlier work.
    if not args.write and os.path.exists(args.out):
        prev = load_json(args.out, {}) or {}
        if isinstance(prev, dict):
            merged_prev = dict(graded)
            merged_prev.update(prev)
            graded = merged_prev

    now = now_ms()
    todo = []
    for sig_id, sig in signals.items():
        if sig_id in graded:
            continue
        interval = str(sig.get("interval") or "")
        if args.interval_filter and interval != args.interval_filter:
            continue
        if not is_mature(sig, interval, cfg, now):
            continue
        todo.append(sig)
    # oldest first so the sample fills in chronologically
    todo.sort(key=lambda s: str(s.get("ts") or ""))
    todo = todo[: max(0, args.limit)]

    print(f"signals={len(signals)} graded={len(graded)} eligible={len(todo)} "
          f"(limit={args.limit}, workers={args.workers})")
    if not todo:
        print("nothing to backfill")
        return 0

    graded_new: dict[str, dict] = {}
    failures = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for sig, result in zip(todo, pool.map(lambda s: grade_one(s, cfg), todo)):
            if result is None:
                failures += 1
                continue
            graded_new[sig["id"]] = result
    elapsed = time.time() - started

    merged = dict(graded)
    merged.update(graded_new)
    target = args.outcomes if args.write else args.out
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=1, sort_keys=True)

    ok = len(graded_new)
    print(f"graded={ok} skipped={len(todo) - ok - failures} failed={failures} "
          f"in {elapsed:.1f}s ({ok / max(elapsed, 0.001):.1f}/s)")
    print(f"outcomes total: {len(graded)} -> {len(merged)}")
    print(f"wrote {target}{' (LIVE state)' if args.write else ' (dry run)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
