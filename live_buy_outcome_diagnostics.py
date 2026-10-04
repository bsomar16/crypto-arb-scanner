#!/usr/bin/env python3
"""Research-only diagnostics for realized BUY outcomes in the paper trade journal.

This does not change signal qualification. It describes closed BUY outcomes by
interval, setup, score/risk/potential bands, and whether TP1 was reached before
the final outcome.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def band(value: float | None, edges: list[float]) -> str:
    if value is None:
        return "unknown"
    for lo, hi in zip(edges, edges[1:]):
        if lo <= value < hi:
            return f"{lo:g}-{hi:g}"
    return f"{edges[-1]:g}+"


def summarize(items):
    closed = len(items)
    wins = sum(1 for x in items if x["outcome"] == "WIN")
    losses = sum(1 for x in items if x["outcome"] == "LOSS")
    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "precision_pct": round(100 * wins / closed, 2) if closed else None,
        "pnl_pct": round(sum(x.get("realized_pnl_pct", 0.0) for x in items), 4),
        "tp1_before_close": sum(1 for x in items if x.get("tp1_hit")),
    }


def main() -> None:
    rows = load_jsonl(Path("state/trade_journal.jsonl"))
    opens = {
        x.get("position_id"): x
        for x in rows
        if x.get("event") == "open"
        and x.get("source") == "buy"
        and x.get("position_id")
    }

    trades = []
    for x in rows:
        if x.get("event") != "close" or x.get("outcome") not in {"WIN", "LOSS"}:
            continue
        opening = opens.get(x.get("position_id"))
        if not opening:
            continue
        trades.append(
            {
                "position_id": x["position_id"],
                "coin": opening.get("coin"),
                "interval": opening.get("interval", "unknown"),
                "setup": opening.get("setup_type", "unknown"),
                "score": float(opening.get("score", 0.0)),
                "potential_pct": float(opening.get("potential_pct", 0.0)),
                "risk_pct": float(opening.get("risk_pct", 0.0)),
                "rr": float(opening.get("rr", 0.0)),
                "outcome": x["outcome"],
                "realized_pnl_pct": float(x.get("realized_pnl_pct", 0.0)),
                "tp1_hit": bool(x.get("tp1_hit")),
                "opened_at": opening.get("ts"),
                "closed_at": x.get("ts"),
            }
        )

    def grouped(key_fn):
        groups = defaultdict(list)
        for t in trades:
            groups[key_fn(t)].append(t)
        return {k: summarize(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}

    by_interval = grouped(lambda x: x["interval"])
    by_interval_setup = grouped(lambda x: f'{x["interval"]}:{x["setup"]}')
    by_score = grouped(lambda x: band(x["score"], [0, 50, 60, 70, 80, 90, 101]))
    by_potential = grouped(lambda x: band(x["potential_pct"], [0, 5, 10, 20, 40, 80, 101, 301]))
    by_risk = grouped(lambda x: band(x["risk_pct"], [0, 2, 3, 5, 8, 12, 20, 101]))
    by_rr = grouped(lambda x: band(x["rr"], [0, 1.5, 2, 2.5, 3, 4, 101]))

    report = {
        "research_only": True,
        "source": "state/trade_journal.jsonl",
        "closed_buy_trades": len(trades),
        "all": summarize(trades),
        "by_interval": by_interval,
        "by_interval_setup": by_interval_setup,
        "by_score": by_score,
        "by_potential_pct": by_potential,
        "by_risk_pct": by_risk,
        "by_rr": by_rr,
        "promotion_rule": (
            "Descriptive live-paper results must be converted into frozen hypotheses "
            "and independently validated OOS before any production change."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    Path("state/live_buy_outcome_diagnostics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
