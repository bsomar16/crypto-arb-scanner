import json

import signal_history


def test_comparable_stats_exact_setup(tmp_path, monkeypatch):
    path = tmp_path / "signal_history.jsonl"
    monkeypatch.setattr(signal_history, "PATH", str(path))
    base = {"coin": "SOL", "interval": "15m", "setup_type": "PULLBACK", "entry": 100,
            "stop": 98, "target": 108, "score": 80, "potential_pct": 8, "rr": 4}
    for i in range(20):
        s = dict(base, entry=100 + i)
        signal_history.record_signal(s)
        signal_history.record_outcome(s, "WIN" if i < 14 else "LOSS", 108 if i < 14 else 98)
    stats = signal_history.comparable_stats(base, min_samples=20)
    assert stats["sample"] == 20
    assert stats["wins"] == 14
    assert stats["losses"] == 6
    assert stats["win_pct"] == 70.0
    assert stats["scope"] == "exact"


def test_insufficient_history_returns_none(tmp_path, monkeypatch):
    path = tmp_path / "signal_history.jsonl"
    monkeypatch.setattr(signal_history, "PATH", str(path))
    signal = {"coin": "BTC", "interval": "5m", "setup_type": "BREAKOUT", "entry": 100}
    signal_history.record_signal(signal)
    signal_history.record_outcome(signal, "WIN", 106)
    stats = signal_history.comparable_stats(signal, min_samples=20)
    assert stats["sample"] == 1
    assert stats["win_pct"] == 100.0
    assert stats["scope"] == "setup/timeframe"


def test_live_outcome_quality_metrics_are_aggregated(tmp_path, monkeypatch):
    path = tmp_path / "signal_history.jsonl"
    monkeypatch.setattr(signal_history, "PATH", str(path))
    base = {"coin": "BTC", "interval": "5m", "setup_type": "MOMENTUM", "entry": 100,
            "stop": 98, "target": 110, "score": 80, "potential_pct": 10, "rr": 5}
    for i in range(20):
        s = dict(base, entry=100 + i)
        signal_history.record_signal(s)
        signal_history.record_outcome(
            s, "WIN" if i < 15 else "LOSS", 110 if i < 15 else 98,
            details={
                "mfe_pct": 12.0 if i < 15 else 3.0,
                "mae_pct": -1.0 if i < 15 else -2.5,
                "milestones": {"5": i < 16, "10": i < 12, "20": i < 3,
                               "30": False, "50": False, "80": False},
            },
        )
    stats = signal_history.comparable_stats(base, min_samples=20)
    assert stats["sample"] == 20
    assert stats["avg_mfe_pct"] == 9.75
    assert stats["avg_mae_pct"] == -1.375
    assert stats["milestone_rates"]["5"] == 80.0
    assert stats["milestone_rates"]["10"] == 60.0
    assert stats["milestone_rates"]["20"] == 15.0
\n\ndef test_outcome_persists_live_attribution_metadata(tmp_path, monkeypatch):\n    path = tmp_path / "signal_history.jsonl"\n    monkeypatch.setattr(signal_history, "PATH", str(path))\n    signal = {\n        "coin": "SOL", "interval": "15m", "setup_type": "MOMENTUM", "entry": 100,\n        "component_flags": {"compression": True, "reclaim": True, "bos": True},\n    }\n    signal_history.record_signal(signal)\n    signal_history.record_outcome(signal, "WIN", 110, details={"milestones": {"5": True}})\n    rows = signal_history._read()\n    outcome = next(r for r in rows if r.get("kind") == "outcome")\n    assert outcome["outcome_source"] == "live"\n    assert outcome["coin"] == "SOL"\n    assert outcome["interval"] == "15m"\n    assert outcome["setup_type"] == "MOMENTUM"\n    assert outcome["component_flags"]["compression"] is True\n\n\ndef test_backtest_outcome_is_explicitly_non_live(tmp_path, monkeypatch):\n    path = tmp_path / "signal_history.jsonl"\n    monkeypatch.setattr(signal_history, "PATH", str(path))\n    signal = {"coin": "BTC", "interval": "15m", "setup_type": "BREAKOUT", "entry": 100}\n    signal_history.record_signal(signal)\n    signal_history.record_outcome(signal, "WIN", 110, source="backtest")\n    outcome = next(r for r in signal_history._read() if r.get("kind") == "outcome")\n    assert outcome["outcome_source"] == "backtest"\n