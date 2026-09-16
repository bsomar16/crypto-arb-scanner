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
