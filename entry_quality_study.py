#!/usr/bin/env python3
"""Research-only OOS study of 4h entry-quality components.

This never changes production BUY rules. It replays the current 4h live engine,
then applies selective post-signal filters to the same OOS trades so feature
effects can be compared without changing the underlying candidate universe.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

from live_replay import fetch_history, replay_symbol, summarize
from signal_audit import SignalAudit

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE"]
INTERVAL = "4h"

VARIANTS = {
    "baseline": lambda r: True,
    "score_ge_55": lambda r: float(r.get("score", 0)) >= 55,
    "score_ge_60": lambda r: float(r.get("score", 0)) >= 60,
    "entry_quality_ge_80": lambda r: float(r.get("entry_quality", 0)) >= 80,
    "entry_quality_ge_85": lambda r: float(r.get("entry_quality", 0)) >= 85,
    "confirmation_body_ge_055": lambda r: float(r.get("confirmation_body", 0)) >= 0.55,
    "extension_le_2pct": lambda r: float(r.get("entry_extension_pct", 999)) <= 2.0,
    "order_block_bullish": lambda r: bool((r.get("order_block") or {}).get("bullish")),
    "zero_inverse_bullish": lambda r: bool((r.get("zero_inverse") or {}).get("bullish_reversal"))
        or bool((r.get("zero_inverse") or {}).get("bullish_reclaim")),
    "volatility_expanding": lambda r: (r.get("volatility") or {}).get("state") == "EXPANDING",
    "entry_quality_80_order_block": lambda r: float(r.get("entry_quality", 0)) >= 80
        and bool((r.get("order_block") or {}).get("bullish")),
    "entry_quality_80_expanding": lambda r: float(r.get("entry_quality", 0)) >= 80
        and (r.get("volatility") or {}).get("state") == "EXPANDING",
    "entry_quality_80_extension_2pct": lambda r: float(r.get("entry_quality", 0)) >= 80
        and float(r.get("entry_extension_pct", 999)) <= 2.0,
}

def _oos_bounds(cfg, n):
    train = min(int(cfg.get("validation_train_bars", 1500)), n)
    val = min(int(cfg.get("validation_validation_bars", 750)), max(0, n - train))
    return train + val, n

def main():
    with open("config.json", encoding="utf-8") as fh:
        cfg = json.load(fh)

    all_trades = []
    per_symbol = {}
    for symbol in SYMBOLS:
        rows = fetch_history(symbol, INTERVAL, int(cfg.get("backtest_bars", 3000)))
        trend_rows = fetch_history(symbol, "1d", max(500, int(cfg.get("backtest_bars", 3000)) // 8))
        if len(rows) < 200:
            continue
        start, end = _oos_bounds(cfg, len(rows))
        if end - start < 50:
            continue
        audit = SignalAudit()
        trades = replay_symbol(
            symbol, INTERVAL, rows, trend_rows, start, end,
            cfg=deepcopy(cfg), audit=audit, entry_policy={
                "require_retest": True,
                "require_sweep": False,
                "allow_early_retest": False,
            },
        )
        all_trades.extend(trades)
        per_symbol[symbol] = {
            "oos_bars": end - start,
            "signals": len(trades),
            "closed": sum(t.get("outcome") in ("WIN", "LOSS") for t in trades),
            "wins": sum(t.get("outcome") == "WIN" for t in trades),
        }

    baseline = summarize(all_trades)
    reports = []
    for name, predicate in VARIANTS.items():
        selected = [r for r in all_trades if predicate(r)]
        s = summarize(selected)
        reports.append({
            "variant": name,
            "detected_signals": s["signals"],
            "closed": s["closed"],
            "wins": s["wins"],
            "losses": s["losses"],
            "precision_pct": round(s["precision_pct"], 2) if s["precision_pct"] is not None else None,
            "coverage_pct": round(s["signals"] / baseline["signals"] * 100, 2) if baseline["signals"] else None,
            "delta_precision_pp": round(s["precision_pct"] - baseline["precision_pct"], 2)
                if s["precision_pct"] is not None and baseline["precision_pct"] is not None else None,
            "delta_wins": s["wins"] - baseline["wins"],
            "sample_met_20_closed": s["closed"] >= 20,
            "avg_mfe_pct": round(s["avg_mfe_pct"], 3) if s["avg_mfe_pct"] is not None else None,
            "avg_mae_pct": round(s["avg_mae_pct"], 3) if s["avg_mae_pct"] is not None else None,
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "current live-engine 4h replay, same OOS window, post-signal component filters",
        "historical_only": True,
        "warning": "Descriptive OOS evidence only; no future-performance guarantee. Filters are research candidates, not production rules.",
        "baseline": baseline,
        "per_symbol": per_symbol,
        "variants": reports,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    with open("state/oos_4h_entry_quality.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
