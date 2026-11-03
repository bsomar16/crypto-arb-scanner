"""Native SPOT-only multi-timeframe SLS strategy module."""

INTERVALS = ("5m", "15m", "1h", "4h", "1d", "1w")
HTF_MAP = {"5m": "1h", "15m": "4h", "1h": "1d", "4h": "1w", "1d": "1M", "1w": None}
THRESHOLDS = {"Conservative": 90, "Balanced": 86, "Aggressive": 82}


def evaluate_sls_coin(coin, interval="1h", cfg=None, chg24=0.0):
    """SLS implementation is built up in isolated, testable stages."""
    return None
