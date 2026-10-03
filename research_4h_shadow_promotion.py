#!/usr/bin/env python3
"""Research-only promotion report for the frozen 4h forward-shadow hypotheses.

This module does not change signal generation or execution. It reads the
research-only forward-shadow ledger and reports sample size, precision,
Wilson 95% intervals, comparison with the baseline, and the separate 90%
research target.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

STATE_PATH = Path("state/forward_shadow_4h.json")
REPORT_PATH = Path("state/forward_shadow_4h_promotion_report.json")
MIN_CLOSED = 20
TARGET_PRECISION_PCT = 90.0


def wilson_interval(wins: int, closed: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if closed <= 0:
        return None, None
    p = wins / closed
    denom = 1.0 + (z * z) / closed
    center = (p + (z * z) / (2.0 * closed)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / closed) + (z * z) / (4.0 * closed * closed)) / denom
    return max(0.0, center - margin) * 100.0, min(1.0, center + margin) * 100.0


def evaluate(candidate: str, summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    closed = int(summary.get("closed_decisive", 0) or 0)
    wins = int(summary.get("wins", 0) or 0)
    precision = summary.get("precision_pct")
    baseline_precision = baseline.get("precision_pct")
    lower, upper = wilson_interval(wins, closed)

    sample_ok = closed >= MIN_CLOSED
    baseline_ok = (
        precision is not None
        and baseline_precision is not None
        and float(precision) >= float(baseline_precision)
    )
    target_90_met = precision is not None and float(precision) >= TARGET_PRECISION_PCT

    if not sample_ok:
        status = "INSUFFICIENT_SAMPLE"
    elif not baseline_ok:
        status = "BELOW_BASELINE"
    else:
        status = "RESEARCH_ELIGIBLE"

    return {
        "candidate": candidate,
        "tracked": int(summary.get("tracked", 0) or 0),
        "closed_decisive": closed,
        "wins": wins,
        "losses": int(summary.get("losses", 0) or 0),
        "expired": int(summary.get("expired", 0) or 0),
        "precision_pct": precision,
        "wilson_95_lower_pct": round(lower, 2) if lower is not None else None,
        "wilson_95_upper_pct": round(upper, 2) if upper is not None else None,
        "baseline_precision_pct": baseline_precision,
        "delta_vs_baseline_pp": (
            round(float(precision) - float(baseline_precision), 2)
            if precision is not None and baseline_precision is not None
            else None
        ),
        "sample_min": MIN_CLOSED,
        "sample_ok": sample_ok,
        "baseline_preserved": baseline_ok,
        "target_90_pct": TARGET_PRECISION_PCT,
        "target_90_met": target_90_met,
        "status": status,
    }


def build_report(state: dict[str, Any]) -> dict[str, Any]:
    summaries = state.get("summaries") or {}
    baseline = summaries.get("baseline") or {}
    candidates = state.get("candidates") or list(summaries.keys())
    return {
        "research_only": True,
        "production_change": False,
        "target_90_is_research_target_only": True,
        "minimum_closed_for_research_review": MIN_CLOSED,
        "candidates": [
            evaluate(str(candidate), summaries.get(candidate) or {}, baseline)
            for candidate in candidates
        ],
        "source_runs": int(state.get("runs", 0) or 0),
        "last_run_at": state.get("last_run_at"),
        "note": (
            "RESEARCH_ELIGIBLE means the frozen candidate has at least 20 "
            "decisive forward-shadow outcomes and has not fallen below the "
            "same-ledger baseline. It is not an authorization to change "
            "production BUY rules or execute trades."
        ),
    }


def main() -> None:
    if not STATE_PATH.exists():
        raise SystemExit(f"Missing research state: {STATE_PATH}")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    report = build_report(state)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
