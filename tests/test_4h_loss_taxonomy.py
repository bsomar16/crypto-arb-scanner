from oos_4h_loss_taxonomy import classify_loss


def test_classify_immediate_failed_followthrough():
    assert classify_loss({"mfe_pct": 0.4, "mae_pct": -1.2}) == "immediate_failed_followthrough"


def test_classify_low_followthrough():
    row = {"mfe_pct": 1.0, "mae_pct": -1.8, "potential_pct": 12.0}
    assert classify_loss(row) == "low_followthrough_vs_target"


def test_classify_late_reversal():
    row = {"mfe_pct": 6.0, "mae_pct": -2.0, "potential_pct": 10.0, "rr": 1.5}
    assert classify_loss(row) == "late_reversal_after_progress"


def test_classify_high_mfe_stop_failure():
    row = {"mfe_pct": 5.0, "mae_pct": -2.2, "potential_pct": 8.0, "rr": 1.5}
    assert classify_loss(row) == "high_mfe_but_stop_failure"


def test_classify_fast_stopout():
    row = {"mfe_pct": 2.0, "mae_pct": -1.7, "estimated_hold_hours": 4}
    assert classify_loss(row) == "fast_stopout"
