#!/usr/bin/env python3
"""
Runs the daily signal analysis and writes
dashboard/public/data/signals.json for the Next.js dashboard.

Called from the repo root by GitHub Actions:
  python dashboard/export_signals.py

It imports the analysis functions directly from scanner.py (repo root)
so there is no duplicated logic.
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# ── Make sure scanner.py (repo root) is importable ────────────────────────────
ROOT = Path(__file__).resolve().parent.parent   # repo root
sys.path.insert(0, str(ROOT))

# These functions are copy-paste-free — we pull them straight from scanner.py
from scanner import (
    http_json,
    fetch_binance_24h,
    analyze_coin,
)
import concurrent.futures

TOP_N   = 20
MIN_QV  = 1_500_000   # $1.5M 24h quote volume minimum


def main():
    out_path = ROOT / "dashboard" / "public" / "data" / "signals.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Fetching Binance 24h tickers…")
    t24_all = fetch_binance_24h()
    if not t24_all:
        print("ERROR: could not fetch tickers", file=sys.stderr)
        sys.exit(1)

    cands = []
    for x in t24_all:
        s = x["symbol"]
        if not (s.endswith("USDT") and s != "USDTUSDT"):
            continue
        base = s[:-4]
        if "USDT" in base or "FDUSD" in base:
            continue
        try:
            qv = float(x["quoteVolume"])
        except (ValueError, KeyError):
            continue
        if qv >= MIN_QV:
            cands.append((base, qv))

    cands.sort(key=lambda r: -r[1])
    top = [c for c, _ in cands[:80]]
    print(f"Candidate pool: {len(top)} coins")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(analyze_coin, top))

    results = [r for r in results if r and r["rating"] in ("BUY", "STRONG BUY")]
    results.sort(key=lambda r: -r["score"])
    results = results[:TOP_N]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scanned": len(top),
        "signals": results,
    }

    out_path.write_text(json.dumps(payload, indent=2))
    strong = sum(1 for r in results if r["rating"] == "STRONG BUY")
    buy    = sum(1 for r in results if r["rating"] == "BUY")
    print(f"Written {strong} STRONG BUY + {buy} BUY → {out_path}")


if __name__ == "__main__":
    main()
