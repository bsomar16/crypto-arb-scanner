#!/usr/bin/env python3
"""Generate compact backtest statistics consumed by live signal alerts."""

import json
import os
from datetime import datetime, timezone

import backtest
from botutil import load_json

OUT = "state/backtest_stats.json"


def main():
    cfg = load_json("config.json", {}) or {}
    symbols = [str(x).upper() for x in (cfg.get("backtest_symbols", []) or ["BTC", "ETH", "SOL"])]
    intervals = tuple(x for x in cfg.get("backtest_intervals", backtest.INTERVALS) if x in backtest.INTERVALS) or backtest.INTERVALS
    bars = int(cfg.get("backtest_bars", 3000))
    kwargs = dict(
        min_vol_x=float(cfg.get("buy_min_vol_x", 1.15)),
        min_potential_pct=float(cfg.get("signal_min_potential_pct", 5.0)),
        max_potential_pct=float(cfg.get("signal_max_potential_pct", 80.0)),
        min_score=float(cfg.get("signal_min_score", 55)),
        min_rr=float(cfg.get("signal_min_rr", 1.5)),
    )
    stats = {}
    for symbol in symbols:
        for interval in intervals:
            try:
                result = backtest.run_symbol(symbol, interval=interval, bars=bars, **kwargs)
                if result:
                    key = f"{symbol}|{interval}"
                    stats[key] = {
                        "symbol": symbol,
                        "interval": interval,
                        "setups": result.get("n", 0),
                        "wins": result.get("wins", 0),
                        "losses": result.get("losses", 0),
                        "expired": result.get("expired", 0),
                        "win_pct": result.get("win_pct"),
                        "avg_win": result.get("avg_win"),
                        "avg_loss": result.get("avg_loss"),
                        "profit_factor": result.get("pf"),
                        "avg_potential": result.get("avg_potential"),
                        "avg_rr": result.get("avg_rr"),
                    }
                    print(f"[BACKTEST-STATS] {key}: {stats[key]}")
            except Exception as exc:
                print(f"[BACKTEST-STATS] {symbol} {interval}: error {exc}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bars": bars,
        "source": "Binance historical OHLCV",
        "cost_pct_round_trip": backtest.COST_PCT,
        "stats": stats,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"[BACKTEST-STATS] wrote {OUT} ({len(stats)} rows)")


if __name__ == "__main__":
    main()
