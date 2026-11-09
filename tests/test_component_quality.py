import component_quality


def _stats(win_pct, sample=40):
    return {"sample": sample, "win_pct": win_pct, "milestone_rates": {"5": win_pct, "10": win_pct * 0.8, "20": win_pct * 0.5}}


def test_mature_live_component_quality_is_bounded():
    signal = {"component_flags": {"reclaim": True, "bos": True}}
    attribution = {"components": {"reclaim": _stats(80), "bos": _stats(75)}, "combinations": {}}
    value = component_quality.ranking_modifier(signal, attribution)
    assert 0 < value <= 4


def test_sparse_components_have_no_effect():
    signal = {"component_flags": {"reclaim": True}}
    attribution = {"components": {"reclaim": _stats(90, 19)}, "combinations": {}}
    assert component_quality.ranking_modifier(signal, attribution) == 0.0


def test_weak_live_evidence_can_only_reduce_within_bound():
    signal = {"component_flags": {"reclaim": True}}
    attribution = {"components": {"reclaim": _stats(10)}, "combinations": {}}
    value = component_quality.ranking_modifier(signal, attribution)
    assert -4 <= value < 0


def test_mature_combination_gets_bounded_evidence():
    signal = {"component_flags": {"liquidity_sweep": True, "reclaim": True, "bos": True}}
    attribution = {
        "components": {"reclaim": _stats(55)},
        "combinations": {"liquidity_sweep+reclaim+bos": _stats(90)},
    }
    assert component_quality.ranking_modifier(signal, attribution) > 0
