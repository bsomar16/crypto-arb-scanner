import json

from research.score_100_outcome_audit import is_perfect_score, summarize


def test_requires_both_score_fields_to_equal_100():
    assert is_perfect_score({"score": 100, "confidence_score": 100})
    assert not is_perfect_score({"score": 100, "confidence_score": 99})
    assert not is_perfect_score({"score": 99, "confidence_score": 100})


def test_summary_joins_outcomes_and_uses_only_decisive_win_rate():
    signals = [
        {"kind": "signal", "id": "a", "score": 100, "confidence_score": 100,
         "interval": "15m", "setup_type": "BREAKOUT"},
        {"kind": "signal", "id": "b", "score": 100, "confidence_score": 100,
         "interval": "15m", "setup_type": "BREAKOUT"},
        {"kind": "signal", "id": "c", "score": 100, "confidence_score": 100,
         "interval": "1h", "setup_type": "MOMENTUM"},
        {"kind": "signal", "id": "d", "score": 100, "confidence_score": 99,
         "interval": "15m", "setup_type": "BREAKOUT"},
    ]
    outcomes = {
        "a": {"outcome": "WIN", "staged_target_hits": {"t1": True, "t3": True}},
        "b": {"outcome": "LOSS", "staged_target_hits": {"t1": False, "t3": False}},
    }
    report = summarize(signals, outcomes)
    assert report["score_100_signals"] == 3
    assert report["overall"]["wins"] == 1
    assert report["overall"]["losses"] == 1
    assert report["overall"]["win_rate_pct"] == 50.0
    assert report["overall"]["unresolved_or_nondecisive"] == 1
    assert report["overall"]["tp1_hit_rate_pct"] == 50.0
    assert report["overall"]["tp3_hit_rate_pct"] == 50.0
