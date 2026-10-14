from oos_4h_baseline_power import metrics_by_symbol


def trade(outcome, entry=100.0, stop=95.0, target=110.0):
    return {"outcome": outcome, "entry": entry, "stop": stop, "t3": target}


def test_metrics_by_symbol_keeps_assets_separate_and_sorted():
    cfg = {"spot_taker_fee_pct": {"binance": 0.1}, "realtime_slippage_reserve_pct": 0.2}
    result = metrics_by_symbol({"ETH": [trade("LOSS")], "BTC": [trade("WIN")]}, cfg)
    assert list(result) == ["BTC", "ETH"]
    assert result["BTC"]["closed"] == 1
    assert result["BTC"]["wins"] == 1
    assert result["ETH"]["closed"] == 1
    assert result["ETH"]["losses"] == 1


def test_metrics_by_symbol_reports_zero_trade_assets_without_fake_precision():
    result = metrics_by_symbol({"SOL": []}, {})
    assert result["SOL"]["signals"] == 0
    assert result["SOL"]["closed"] == 0
    assert result["SOL"]["precision_pct"] is None
    assert result["SOL"]["expectancy_r_after_configured_cost_scenario"] is None
