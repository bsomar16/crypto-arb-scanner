import signal_context


def _bars():
    n = 35
    closes = [100.0] * n
    highs = [101.0] * n
    lows = [99.0] * n
    vols = [100.0] * n

    # Bearish origin candle with a liquidity sweep.
    closes[20] = 98.0
    highs[20] = 101.0
    lows[20] = 97.0

    # Bullish displacement and BOS.
    closes[21] = 102.0
    highs[21] = 103.0
    lows[21] = 99.0
    closes[22] = 104.0
    highs[22] = 105.0
    lows[22] = 101.0
    vols[21] = 220.0
    vols[22] = 240.0

    return closes, highs, lows, vols


def test_order_block_returns_structural_quality_and_timing():
    closes, highs, lows, vols = _bars()
    result = signal_context._order_block(closes, highs, lows, vols, atr=2.0)

    assert result["bullish"] is True
    assert result["fresh"] is True
    assert result["status"] == "FRESH"
    assert result["timing"] == "EARLY"
    assert result["zone_low"] == 97.0
    assert result["zone_high"] == 100.0
    assert result["midpoint"] == 98.5
    assert result["quality_score"] > 50
    assert result["displacement_score"] > 0
    assert result["bos_score"] > 0
    assert result["liquidity_sweep"] is True
    assert result["touches"] == 0
    assert result["invalidation"] == 97.0


def test_order_block_is_invalidated_after_close_below_zone():
    closes, highs, lows, vols = _bars()
    closes[-1] = 96.0
    highs[-1] = 97.0
    lows[-1] = 95.0

    result = signal_context._order_block(closes, highs, lows, vols, atr=2.0)

    assert result["bullish"] is True
    assert result["status"] == "INVALIDATED"
    assert result["timing"] == "INVALID"
    assert result["quality_score"] == 0.0
    assert result["strength"] == 0.0


def test_empty_order_block_preserves_legacy_shape():
    result = signal_context._order_block([1.0] * 10, [1.0] * 10, [1.0] * 10, [1.0] * 10, atr=1.0)

    assert result["bullish"] is False
    assert result["fresh"] is False
    assert result["strength"] == 0.0
    assert result["quality_score"] == 0.0
    assert result["status"] == "NONE"
    assert result["timing"] == "INVALID"
