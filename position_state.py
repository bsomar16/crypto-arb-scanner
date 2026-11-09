#!/usr/bin/env python3
"""Compatibility/migration helpers for persisted tracked-position state."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from botutil import load_json, save_json

POS_FILE = "state/positions.json"


def _legacy_position_id(pos: dict, index: int) -> str:
    """Return a stable ID for positions created before position_id existed."""
    seed = "|".join(
        str(pos.get(key, ""))
        for key in ("coin", "entry", "entry_ts", "source", "rating")
    ) + f"|{index}"
    return f"legacy-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]}"


def migrate_positions() -> int:
    """Add IDs/default metadata to legacy position records and persist once."""
    positions = load_json(POS_FILE, [])
    if not isinstance(positions, list):
        return 0

    changed = 0
    for index, pos in enumerate(positions):
        if not isinstance(pos, dict):
            continue
        if not pos.get("position_id"):
            pos["position_id"] = _legacy_position_id(pos, index)
            changed += 1
        if not pos.get("entry_ts"):
            pos["entry_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            changed += 1
        pos.setdefault("source", "legacy")
        pos.setdefault("mode", "paper")
        pos.setdefault("exchange", "BINANCE")
        pos.setdefault("notional_usdt", 0.0)
        pos.setdefault("tp1_hit", False)
        pos.setdefault("tp2_hit", False)
        pos.setdefault("tp3_hit", False)

    if changed:
        save_json(POS_FILE, positions)
    return changed
