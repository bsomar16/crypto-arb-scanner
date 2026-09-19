#!/usr/bin/env python3
"""Bounded live TP1/TP2/TP3 target-quality adjustment.

The optimizer uses only mature live staged-target reach evidence. It never
creates a signal, removes a signal, or overrides the technical target model.
Reach rates describe price touches; they are not realized-return guarantees.
"""

MIN_SAMPLES = 30
MAX_TARGET_DISTANCE_ADJUSTMENT = 0.12
PRIOR_RATES = {"t1": 70.0, "t2": 45.0, "t3": 25.0}
PRIOR_WEIGHT = 30.0


def _shrink_rate(observed, sample, prior):
    """Empirical-Bayes-style shrinkage toward a conservative prior."""
    n = max(0, int(sample or 0))
    value = float(observed)
    return (value * n + prior * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT)


def _scope_stats(signal, stats, min_samples=MIN_SAMPLES):
    interval = str(signal.get("interval", ""))
    setup = str(signal.get("setup_type", ""))
    scoped = (stats.get("by_scope") or {}).get(f"{interval}|{setup}")
    if scoped and int(scoped.get("sample", 0) or 0) >= int(min_samples):
        return scoped, "exact"
    overall = stats.get("overall")
    if overall and int(overall.get("sample", 0) or 0) >= int(min_samples):
        return overall, "overall"
    return None, None


def optimize_targets(signal, stats, min_samples=MIN_SAMPLES, enabled=True):
    """Return a bounded target plan, leaving the original plan unchanged when
    evidence is immature, unavailable, or unsafe.

    Exact timeframe/setup evidence receives the full adjustment. Overall live
    evidence receives half strength because it is less comparable.
    """
    base_entry = float(signal.get("entry", signal.get("price", 0)) or 0)
    base_t3 = float(signal.get("t3", signal.get("target", 0)) or 0)
    risk_pct = float(signal.get("risk_pct", 0) or 0)
    min_rr = float(signal.get("rr", 0) or 0)
    max_potential = float(signal.get("max_potential_pct", 80.0) or 80.0)
    min_potential = float(signal.get("min_potential_pct", 5.0) or 5.0)

    def unchanged(reason):
        return {
            "t1": float(signal.get("t1", 0) or 0),
            "t2": float(signal.get("t2", 0) or 0),
            "t3": base_t3,
            "target": base_t3,
            "mode": "BASE",
            "adjustment_pct": 0.0,
            "evidence_scope": None,
            "evidence_sample": 0,
            "evidence_rates": {},
            "reason": reason,
        }

    if not enabled or not stats or base_entry <= 0 or base_t3 <= base_entry:
        return unchanged("no mature live target evidence")
    threshold = max(1, int(min_samples))
    scoped, scope = _scope_stats(signal, stats, threshold)
    if not scoped or int(scoped.get("sample", 0) or 0) < threshold:
        return unchanged("insufficient live target evidence")

    rates = scoped.get("hit_rates_pct") or {}
    if not all(k in rates for k in ("t1", "t2", "t3")):
        return unchanged("incomplete target evidence")

    sample = int(scoped.get("sample", 0) or 0)
    shrunk = {
        k: _shrink_rate(rates[k], sample, PRIOR_RATES[k])
        for k in ("t1", "t2", "t3")
    }

    # A target is "too easy" only when reach evidence is materially above the
    # prior at successive stages. Conversely, weak reach evidence supports a
    # modestly closer target. The score is deliberately conservative.
    excess = (
        0.20 * (shrunk["t1"] - PRIOR_RATES["t1"])
        + 0.30 * (shrunk["t2"] - PRIOR_RATES["t2"])
        + 0.50 * (shrunk["t3"] - PRIOR_RATES["t3"])
    ) / 100.0
    adjustment = max(-MAX_TARGET_DISTANCE_ADJUSTMENT,
                     min(MAX_TARGET_DISTANCE_ADJUSTMENT, excess))
    if scope == "overall":
        adjustment *= 0.50

    distance = base_t3 - base_entry
    candidate = base_entry + distance * (1.0 + adjustment)

    # Preserve the strategy's hard potential envelope.
    candidate = min(candidate, base_entry * (1.0 + max_potential / 100.0))
    candidate = max(candidate, base_entry * (1.0 + min_potential / 100.0))

    # Never weaken the already-validated R:R requirement.
    if risk_pct > 0 and min_rr > 0:
        candidate = max(candidate, base_entry * (1.0 + (risk_pct * min_rr) / 100.0))

    # If the constrained result cannot fit the original envelope, keep the
    # technical target rather than manufacturing a target that violates bounds.
    if candidate <= base_entry:
        return unchanged("target constraints prevent adjustment")

    # Preserve staged proportions and ordering.
    t1 = base_entry + (candidate - base_entry) * 0.35
    t2 = base_entry + (candidate - base_entry) * 0.65
    if not (base_entry < t1 < t2 < candidate):
        return unchanged("target staging constraint failed")

    mode = "EXTEND" if adjustment > 0.002 else "TIGHTEN" if adjustment < -0.002 else "BASE"
    return {
        "t1": t1,
        "t2": t2,
        "t3": candidate,
        "target": candidate,
        "mode": mode,
        "adjustment_pct": round(adjustment * 100.0, 2),
        "evidence_scope": scope,
        "evidence_sample": sample,
        "evidence_rates": {k: round(float(rates[k]), 2) for k in ("t1", "t2", "t3")},
        "shrunk_rates": {k: round(v, 2) for k, v in shrunk.items()},
        "reason": f"mature live TP reach evidence ({sample}, {scope})",
    }
