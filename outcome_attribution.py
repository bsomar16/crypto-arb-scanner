#!/usr/bin/env python3
"""Descriptive outcome attribution for causal crypto SPOT signal components."""

COMPONENTS = ("compression", "liquidity_sweep", "reclaim", "bos", "retest",
              "volume_acceleration", "early_expansion", "expansion")
MILESTONES = (5, 10, 20)


def aggregate(records, min_samples=20):
    """Return component milestone rates only when enough live outcomes exist."""
    result = {}
    rows = [r for r in (records or [])
            if str(r.get("outcome", "")).upper() in ("WIN", "LOSS")]
    for component in COMPONENTS:
        chosen = [r for r in rows
                  if bool((r.get("component_flags") or {}).get(component))]
        if len(chosen) < int(min_samples):
            continue
        wins = sum(str(r.get("outcome", "")).upper() == "WIN" for r in chosen)
        rates = {}
        for milestone in MILESTONES:
            key = str(milestone)
            rates[key] = round(
                sum(bool((r.get("milestones") or {}).get(key)) for r in chosen)
                / len(chosen) * 100.0, 2
            )
        result[component] = {
            "sample": len(chosen),
            "wins": wins,
            "losses": len(chosen) - wins,
            "win_pct": round(wins / len(chosen) * 100.0, 2),
            "milestone_rates": rates,
        }
    return result
