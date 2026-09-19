#!/usr/bin/env python3
"""Descriptive live-outcome attribution for causal crypto SPOT components."""

COMPONENTS = (
    "compression", "liquidity_sweep", "reclaim", "bos", "retest",
    "volume_acceleration", "early_expansion", "expansion",
)
COMBINATIONS = (
    ("liquidity_sweep", "reclaim", "bos"),
    ("compression", "volume_acceleration", "early_expansion"),
    ("reclaim", "retest"),
)
MILESTONES = (5, 10, 20)


def _is_live_outcome(row):
    return (
        str(row.get("outcome", "")).upper() in ("WIN", "LOSS")
        and str(row.get("outcome_source", "live")).lower() == "live"
    )


def _stats(rows):
    wins = sum(str(r.get("outcome", "")).upper() == "WIN" for r in rows)
    rates = {}
    for milestone in MILESTONES:
        key = str(milestone)
        rates[key] = round(
            sum(bool((r.get("milestones") or {}).get(key)) for r in rows)
            / len(rows) * 100.0, 2
        )
    return {
        "sample": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "win_pct": round(wins / len(rows) * 100.0, 2),
        "milestone_rates": rates,
    }


def aggregate(records, min_samples=20):
    """Return live component/combo stats, optionally scoped by timeframe/setup.

    This is measurement only. Sparse buckets are omitted and no simulated
    outcomes are mixed into the live evidence.
    """
    rows = [r for r in (records or []) if _is_live_outcome(r)]
    minimum = max(1, int(min_samples))
    result = {"components": {}, "combinations": {}, "buckets": {}}

    def add(key, chosen):
        if len(chosen) < minimum:
            return
        result[key] = _stats(chosen)

    for component in COMPONENTS:
        add(component, [
            r for r in rows
            if bool((r.get("component_flags") or {}).get(component))
        ])

    for combo in COMBINATIONS:
        add("+".join(combo), [
            r for r in rows
            if all(bool((r.get("component_flags") or {}).get(c)) for c in combo)
        ])

    # More actionable attribution: same interval + setup + component.
    intervals = sorted({str(r.get("interval", "")) for r in rows if r.get("interval")})
    setups = sorted({str(r.get("setup_type", "")) for r in rows if r.get("setup_type")})
    for interval in intervals:
        for setup in setups:
            bucket_rows = [
                r for r in rows
                if str(r.get("interval")) == interval
                and str(r.get("setup_type")) == setup
            ]
            for component in COMPONENTS:
                chosen = [
                    r for r in bucket_rows
                    if bool((r.get("component_flags") or {}).get(component))
                ]
                if len(chosen) >= minimum:
                    result["buckets"][f"{interval}|{setup}|{component}"] = _stats(chosen)

    return result
