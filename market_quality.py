"""Spot market-quality and order-book execution intelligence.

Independent from signal generation. Measures visible liquidity, spread and
estimated market-buy slippage for a configured USDT notional. Missing public
depth data fails open and hard blocking is opt-in.
"""

from __future__ import annotations

from markets import fetch_orderbook


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and value >= 0 else None


def _depth_within(levels, reference, band):
    if not levels or reference is None or reference <= 0:
        return 0.0
    limit = reference * (1.0 + band)
    return sum(p * q for p, q in levels if p <= limit)


def _buy_fill(levels, notional):
    """Return VWAP and slippage for a market BUY of the given USDT notional."""
    if not levels or notional <= 0:
        return None
    best = levels[0][0]
    remaining = float(notional)
    spent = 0.0
    qty = 0.0
    for price, amount in levels:
        if price <= 0 or amount <= 0:
            continue
        level_quote = price * amount
        take = min(remaining, level_quote)
        qty += take / price
        spent += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if qty <= 0 or spent <= 0:
        return None
    vwap = spent / qty
    return {
        "vwap": vwap,
        "slippage_pct": max(0.0, (vwap / best - 1.0) * 100.0),
        "filled_pct": min(100.0, spent / notional * 100.0),
    }


def assess(symbol, notional_usdt=None, cfg=None, orderbook=None):
    """Assess Binance SPOT order-book quality for a BUY signal."""
    cfg = cfg or {}
    if not bool(cfg.get("market_quality_enabled", True)):
        return {"state": "DISABLED", "risk_action": "ALLOW",
                "data_available": False, "modifier": 0.0}

    try:
        notional = float(
            notional_usdt if notional_usdt is not None
            else cfg.get("market_quality_notional_usdt", 1000.0)
        )
    except (TypeError, ValueError):
        notional = 1000.0
    notional = max(1.0, notional)

    try:
        depth = orderbook if orderbook is not None else fetch_orderbook(
            "BINANCE", str(symbol).upper(),
            limit=int(cfg.get("market_quality_depth", 50))
        )
    except Exception:
        depth = None

    if not depth:
        return {"state": "UNKNOWN", "risk_action": "ALLOW",
                "data_available": False, "modifier": 0.0,
                "reason": "orderbook_unavailable"}

    asks = depth.get("asks") or []
    bids = depth.get("bids") or []
    if not asks or not bids:
        return {"state": "UNKNOWN", "risk_action": "ALLOW",
                "data_available": False, "modifier": 0.0,
                "reason": "orderbook_incomplete"}

    ask = _finite(asks[0][0])
    bid = _finite(bids[0][0])
    if not ask or not bid or ask <= 0 or bid <= 0:
        return {"state": "UNKNOWN", "risk_action": "ALLOW",
                "data_available": False, "modifier": 0.0,
                "reason": "invalid_top_of_book"}

    spread_pct = max(0.0, (ask / bid - 1.0) * 100.0)
    depth_25 = _depth_within(asks, ask, 0.0025)
    depth_50 = _depth_within(asks, ask, 0.005)
    depth_100 = _depth_within(asks, ask, 0.01)
    fill = _buy_fill(asks, notional)

    max_spread = max(0.01, float(cfg.get("market_quality_max_spread_pct", 0.35)))
    max_slippage = max(0.01, float(cfg.get("market_quality_max_slippage_pct", 0.25)))
    min_depth = max(0.0, float(cfg.get("market_quality_min_depth_usdt", 25000.0)))

    slippage = float(fill["slippage_pct"]) if fill else None
    filled_pct = float(fill["filled_pct"]) if fill else 0.0
    wide = spread_pct > max_spread
    thin = depth_50 < min_depth or filled_pct < 100.0
    slip_bad = slippage is not None and slippage > max_slippage

    if wide:
        state = "WIDE_SPREAD"
    elif thin or slip_bad:
        state = "THIN"
    else:
        state = "LIQUID"

    modifier = 2.0 if state == "LIQUID" else -2.0 if state == "THIN" else -3.0
    hard_block = bool(cfg.get("market_quality_hard_block", False))
    risk_action = "BLOCK" if hard_block and state in ("THIN", "WIDE_SPREAD") else "ALLOW"

    return {
        "state": state, "risk_action": risk_action, "data_available": True,
        "spread_pct": round(spread_pct, 4),
        "estimated_slippage_pct": round(slippage, 4) if slippage is not None else None,
        "filled_pct": round(filled_pct, 2),
        "depth_25_usdt": round(depth_25, 2),
        "depth_50_usdt": round(depth_50, 2),
        "depth_100_usdt": round(depth_100, 2),
        "depth_usdt": round(depth_50, 2),
        "best_bid": bid, "best_ask": ask,
        "vwap": round(float(fill["vwap"]), 12) if fill else None,
        "notional_usdt": round(notional, 2), "modifier": modifier,
        "reason": "within configured market-quality limits"
        if state == "LIQUID" else "spread/depth/slippage requires review",
    }
