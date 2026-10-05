from oos_4h_momentum_expansion import CANDIDATES

def test_candidate_matrix_is_frozen():
    assert list(CANDIDATES) == [
        "baseline",
        "exclude_momentum_expansion",
        "non_expansion",
        "structure_ge_50_non_expansion",
        "bullish_non_expansion",
    ]

def test_momentum_expansion_is_excluded_only_when_both_match():
    r={"setup_type":"MOMENTUM","expansion_state":"EXPANSION"}
    assert CANDIDATES["exclude_momentum_expansion"](r) is False
    r={"setup_type":"PULLBACK","expansion_state":"EXPANSION"}
    assert CANDIDATES["exclude_momentum_expansion"](r) is True
