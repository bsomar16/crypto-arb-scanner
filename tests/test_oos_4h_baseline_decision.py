"""Regression tests for research-only 4h baseline decision gates."""
from oos_4h_baseline_power import decision_for


def metrics(closed=100, expectancy=0.1, profit_factor=1.2, wilson=35.0, breakeven=30.0):
    return {
        "closed": closed,
        "expectancy_r_after_configured_cost_scenario": expectancy,
        "profit_factor": profit_factor,
        "wilson_95_lower_pct": wilson,
        "cost_adjusted_breakeven_win_rate_pct": breakeven,
    }


def test_insufficient_sample_takes_precedence():
    assert decision_for(metrics(closed=99), [metrics(closed=20)] * 3) == "INSUFFICIENT SAMPLE"
    assert decision_for(metrics(), [metrics(closed=19), metrics(), metrics()]) == "INSUFFICIENT SAMPLE"


def test_negative_pooled_expectancy_fails():
    assert decision_for(metrics(expectancy=-0.01), [metrics()] * 3) == "FAIL — NON-POSITIVE OR UNSTABLE EXPECTANCY"


def test_negative_window_expectancy_fails_even_if_pooled_is_positive():
    assert decision_for(metrics(), [metrics(), metrics(expectancy=-0.01), metrics()]) == "FAIL — NON-POSITIVE OR UNSTABLE EXPECTANCY"


def test_profit_factor_at_or_below_one_fails():
    assert decision_for(metrics(profit_factor=1.0), [metrics()] * 3) == "FAIL — NON-POSITIVE OR UNSTABLE EXPECTANCY"


def test_wilson_lower_bound_must_clear_cost_adjusted_breakeven():
    assert decision_for(metrics(wilson=30.0, breakeven=30.0), [metrics()] * 3) == "FAIL — WIN-RATE CONFIDENCE GATE"
    assert decision_for(metrics(wilson=29.0, breakeven=30.0), [metrics()] * 3) == "FAIL — WIN-RATE CONFIDENCE GATE"


def test_positive_evidence_is_research_only_not_production_approval():
    assert decision_for(metrics(), [metrics()] * 3) == "RESEARCH PASS — NOT A PRODUCTION APPROVAL"
