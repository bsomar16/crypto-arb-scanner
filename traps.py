#!/usr/bin/env python3
"""Trap registry: which coins were recently flagged, with expiry + migration.

Stores {coin: first_flagged_ts} so a coin stays quiet while its gap is
"known", but is allowed to re-alert once TRAP_EXPIRY_DAYS passes.
Auto-migrates the old flat-list format on first load.
"""

import json
import time


def load_traps(path, expiry_days=7.0, now=None):
    """Load trap map {coin: first_flagged_ts}, pruning expired entries.

    Migrates the legacy flat list ["COIN", ...] to the timestamp map
    (entries get `now` as their first-flagged time).
    """
    now = now if now is not None else time.time()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if isinstance(raw, dict):
        traps = {}
        for k, v in raw.items():
            try:
                traps[str(k)] = float(v)
            except (TypeError, ValueError):
                traps[str(k)] = now
        return prune_expired_traps(traps, expiry_days, now=now)
    if isinstance(raw, list):
        out = {}
        for coin in raw:
            if isinstance(coin, str) and coin:
                out.setdefault(coin, now)
        return prune_expired_traps(out, expiry_days, now=now)
    return {}


def prune_expired_traps(traps, expiry_days=7.0, now=None):
    """Drop coins first-flagged more than expiry_days ago. Pure function."""
    now = now if now is not None else time.time()
    cutoff = now - float(expiry_days) * 86400
    return {k: ts for k, ts in traps.items() if ts >= cutoff}


def mark_flagged(traps, coins, now=None):
    """Tag coins as flagged, keeping each coin's original first-flag ts."""
    now = now if now is not None else time.time()
    for coin in coins:
        traps.setdefault(coin, now)
    return traps


def save_traps(path, traps):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(traps, f, indent=1, sort_keys=True)