import outcome_attribution


def _outcome(source="live", flags=None, interval="15m", setup="MOMENTUM", win=True):
    return {
        "kind": "outcome", "outcome": "WIN" if win else "LOSS",
        "outcome_source": source, "interval": interval, "setup_type": setup,
        "component_flags": flags or {},
        "milestones": {"5": win, "10": win, "20": False},
    }


def test_aggregate_uses_persisted_live_component_metadata():
    flags = {"compression": True, "liquidity_sweep": True, "reclaim": True, "bos": True}
    rows = [_outcome(flags=flags, win=i < 15) for i in range(20)]
    rows.append(_outcome(source="backtest", flags=flags, win=True))
    stats = outcome_attribution.aggregate(rows, min_samples=20)
    assert stats["components"]["compression"]["sample"] == 20
    assert stats["components"]["compression"]["win_pct"] == 75.0
    assert stats["combinations"]["liquidity_sweep+reclaim+bos"]["sample"] == 20
    assert stats["buckets"]["15m|MOMENTUM|reclaim"]["sample"] == 20


def test_sparse_component_buckets_are_omitted():
    rows = [_outcome(flags={"retest": True}) for _ in range(19)]
    stats = outcome_attribution.aggregate(rows, min_samples=20)
    assert "retest" not in stats["components"]


def test_non_live_outcomes_never_contribute():
    flags = {"early_expansion": True}
    rows = [_outcome(source="backtest", flags=flags) for _ in range(20)]
    stats = outcome_attribution.aggregate(rows, min_samples=20)
    assert "early_expansion" not in stats["components"]
