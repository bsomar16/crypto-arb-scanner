#!/usr/bin/env python3
"""Bounded live component evidence for crypto SPOT signal ranking.

This module converts sufficiently mature live attribution into a small,
confidence-shrunk ranking modifier. It never creates or removes signals.
"""

MIN_SAMPLES = 30
PRIOR_WIN_PCT = 50.0
MAX_MODIFIER = 4.0
COMPONENTS = (
    "compression", "liquidity_sweep", "reclaim", "bos", "retest",
    "volume_acceleration", "early_expansion", "expansion",
)


def _smoothed_win(stats, prior=PRIOR_WIN_PCT):
    sample = int(stats.get("sample", 0) or 0)
    win = float(stats.get("win_pct", prior) or prior)
    return (win * sample + prior * MIN_SAMPLES) / (sample + MIN_SAMPLES)


def _component_score(stats):
    smoothed = _smoothed_win(stats)
    rates = stats.get("milestone_rates") or {}
    early = (
        float(rates.get("5", 0.0)) * 0.50
        + float(rates.get("10", 0.0)) * 0.30
        + float(rates.get("20", 0.0)) * 0.20
    )
    return smoothed * 0.70 + early * 0.30


def ranking_modifier(signal, attribution, min_samples=MIN_SAMPLES):
    """Return a bounded descriptive modifier; zero for immature evidence."""
    flags = signal.get("component_flags") or {}
    components = (attribution or {}).get("components") or {}
    combinations = (attribution or {}).get("combinations") or {}
    minimum = max(1, int(min_samples))

    candidates = []
    for component in COMPONENTS:
        if not flags.get(component):
            continue
        stats = components.get(component)
        if stats and int(stats.get("sample", 0) or 0) >= minimum:
            candidates.append(_component_score(stats))

    if not candidates:
        return 0.0

    # A combination is useful only when it has its own mature evidence.
    for key, stats in combinations.items():
        parts = key.split("+")
        if all(flags.get(part) for part in parts) and int(stats.get("sample", 0) or 0) >= minimum:
            candidates.append(_component_score(stats))

    evidence = sum(candidates) / len(candidates)
    modifier = (evidence - 50.0) / 12.5
    return round(max(-MAX_MODIFIER, min(MAX_MODIFIER, modifier)), 2)
