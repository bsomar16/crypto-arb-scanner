#!/usr/bin/env python3
"""Correlation-aware exposure control for crypto spot BUY selection.

This is intentionally a market-behaviour proxy rather than a claimed sector
taxonomy: crypto assets do not have a reliable universal sector classification.
"""

from concurrent.futures import ThreadPoolExecutor
from math import sqrt

from botutil import http_json

BN = "https://data-api.binance.vision"


def _returns(coin, interval="1h", limit=72):
    try:
        data = http_json(
            f"{BN}/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}",
            timeout=10,
        )
        closes = [float(k[4]) for k in data[:-1]]
        if len(closes) < 20:
            return None
        return [(b / a - 1.0) for a, b in zip(closes[:-1], closes[1:]) if a > 0]
    except Exception:
        return None


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / sqrt(va * vb)


def select_diversified(ranked, cfg):
    """Select a daily entry set while preserving ranking priority.

    The first qualified signal is always eligible. Subsequent signals are
    accepted unless they exceed the configured correlation threshold with an
    already selected coin. We never manufacture a trade to fill the budget.
    """
    budget = max(0, min(int(cfg.get("max_trade_entries_per_day", 5)), 5))
    if budget <= 1 or len(ranked) <= 1:
        return ranked[:budget], []

    threshold = float(cfg.get("max_pairwise_correlation", 0.88))
    lookback = int(cfg.get("correlation_lookback_bars", 72))
    interval = str(cfg.get("correlation_interval", "1h"))

    coins = []
    for r in ranked:
        coin = str(r.get("coin", "")).upper()
        if coin and coin not in coins:
            coins.append(coin)

    workers = max(2, min(12, int(cfg.get("correlation_workers", 8))))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        values = list(ex.map(lambda c: (c, _returns(c, interval, lookback)), coins))
    returns = dict(values)

    selected, blocked = [], []
    for candidate in ranked:
        coin = str(candidate.get("coin", "")).upper()
        if not coin:
            continue
        conflicts = []
        for existing in selected:
            other = str(existing.get("coin", "")).upper()
            a, b = returns.get(coin), returns.get(other)
            if a and b and _corr(a, b) >= threshold:
                conflicts.append({"coin": other, "correlation": round(_corr(a, b), 3)})
        if conflicts:
            row = dict(candidate)
            row["exposure_blocked"] = True
            row["exposure_conflicts"] = conflicts
            blocked.append(row)
            continue
        selected.append(candidate)
        if len(selected) >= budget:
            break

    return selected, blocked
