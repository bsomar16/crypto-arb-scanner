#!/usr/bin/env python3
"""Persistent, provider-neutral trade journal for signal and position outcomes.

The journal is append-only JSONL so restarts cannot erase history. It supports
both virtual/paper positions and live executions; live order identifiers and
fills can be attached without changing the statistics model.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

PATH = "state/trade_journal.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(event: str, position_id: str, **fields: Any) -> dict:
    row = {"ts": _now(), "event": str(event), "position_id": str(position_id)}
    for key, value in fields.items():
        if value is not None:
            row[key] = value
    Path(PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def read() -> list[dict]:
    rows: list[dict] = []
    try:
        with open(PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _positions(rows: list[dict]) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for row in rows:
        pid = row.get("position_id")
        if not pid:
            continue
        item = state.setdefault(pid, {"position_id": pid})
        item.update({k: v for k, v in row.items() if k not in {"ts", "event", "position_id"}})
        item["last_event"] = row.get("event")
        item["last_ts"] = row.get("ts")
    return state


def statistics(rows: list[dict] | None = None) -> dict:
    """Return descriptive outcome statistics; no predictive claims."""
    state = _positions(rows if rows is not None else read())
    closed = [p for p in state.values() if p.get("status") in {"closed_tp3", "closed_sl", "expired", "closed"}]
    wins = [p for p in closed if p.get("outcome") == "WIN"]
    losses = [p for p in closed if p.get("outcome") == "LOSS"]
    pnl = []
    for p in closed:
        try:
            pnl.append(float(p.get("realized_pnl_pct")))
        except (TypeError, ValueError):
            pass
    tp1 = sum(bool(p.get("tp1_hit")) for p in state.values())
    tp2 = sum(bool(p.get("tp2_hit")) for p in state.values())
    tp3 = sum(bool(p.get("tp3_hit")) for p in state.values())
    return {
        "tracked": len(state),
        "open": sum(p.get("status") == "open" for p in state.values()),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "expired": sum(p.get("status") == "expired" for p in state.values()),
        "win_rate": len(wins) / (len(wins) + len(losses)) if wins or losses else None,
        "avg_realized_pnl_pct": mean(pnl) if pnl else None,
        "tp1_hits": tp1,
        "tp2_hits": tp2,
        "tp3_hits": tp3,
    }
