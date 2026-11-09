"""Cross-exchange SPOT market intelligence.

Uses public spot tickers only. It reports venue consensus and executable
price dispersion; it never creates a trade by itself.
"""

from __future__ import annotations

from markets import EXCHANGES, fetch_exchange, taker_fee


CORE_SPOT_EXCHANGES = ("BINANCE", "BYBIT", "OKX", "BITGET", "MEXC")


def assess(coin, cfg=None, price_maps=None):
    cfg = cfg or {}
    if not bool(cfg.get("multi_exchange_enabled", True)):
        return {"state": "DISABLED", "data_available": False, "modifier": 0.0}

    maps = price_maps
    if maps is None:
        maps = {}
        for ex in CORE_SPOT_EXCHANGES:
            try:
                maps[ex] = fetch_exchange(ex)
            except Exception:
                maps[ex] = {}

    prices = {}
    symbol = str(coin or "").upper()
    for ex in CORE_SPOT_EXCHANGES:
        try:
            p = float((maps.get(ex) or {}).get(symbol))
            if p > 0:
                prices[ex] = p
        except (TypeError, ValueError):
            continue

    if len(prices) < 2:
        return {
            "state": "UNKNOWN", "data_available": False, "modifier": 0.0,
            "exchange_count": len(prices), "prices": prices,
        }

    vals = list(prices.values())
    low_ex = min(prices, key=prices.get)
    high_ex = max(prices, key=prices.get)
    low = prices[low_ex]
    high = prices[high_ex]
    dispersion = (high / low - 1.0) * 100.0 if low > 0 else 0.0

    max_dispersion = max(0.01, float(cfg.get("multi_exchange_max_dispersion_pct", 1.5)))
    state = "CONSISTENT" if dispersion <= max_dispersion else "DISPERSION"

    # Prefer venues that are both inside the configured core set and have a
    # quoted price. This is informational; actual execution must revalidate.
    fee_adjusted_low = low * (1.0 + taker_fee(low_ex))
    fee_adjusted_high = high * (1.0 - taker_fee(high_ex))
    executable_gap = max(0.0, (fee_adjusted_high / fee_adjusted_low - 1.0) * 100.0)

    modifier = 1.0 if state == "CONSISTENT" else -1.0
    return {
        "state": state,
        "data_available": True,
        "modifier": modifier,
        "exchange_count": len(prices),
        "prices": prices,
        "low_exchange": low_ex,
        "high_exchange": high_ex,
        "low_price": low,
        "high_price": high,
        "price_dispersion_pct": round(dispersion, 4),
        "fee_adjusted_gap_pct": round(executable_gap, 4),
        "reason": "cross-exchange prices agree"
        if state == "CONSISTENT" else "cross-exchange price dispersion requires revalidation",
    }
