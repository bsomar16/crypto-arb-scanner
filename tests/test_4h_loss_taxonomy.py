from oos_4h_loss_taxonomy import classify_loss  # research-only taxonomy


def test_classify_immediate_failed_followthrough():
    assert classify_loss({"mfe_pct": 0.4, "mae_pct": -1.2}) == "immediate_failed_followthrough"


def test_classify_low_followthrough():
    assert classify_loss({"mfe_pct": 1.0, "mae_pct": -1.8, "potential_pct": 12.0}) == "low_followthrough_vs_target"


def test_classify_late_reversal():
    assert classify_loss({"mfe_pct": 6.0, "mae_pct": -2.0, "potential_pct": 10.0, "rr": 1.5}) == "late_reversal_after_progress"


def test_classify_high_mfe_stop_failure():
    assert classify_loss({"mfe_pct": 8.0, "mae_pct": -2.2, "potential_pct": 12.0}) == "high_mfe_but_stop_failure"


def test_classify_fast_stopout():
    assert classify_loss({"mfe_pct": 2.0, "mae_pct": -1.7, "estimated_hold_hours": 4}) == "fast_stopout"
