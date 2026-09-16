#!/usr/bin/env python3
"""Executable-SPOT-arbitrage opportunity calculations.

The engine is intentionally conservative. BBO evaluation is retained for fast
candidate discovery; full-depth evaluation is available before confirmation
so a spread is not treated as executable merely because the top quote is large.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence

from realtime_market import BBO
from orderbook_depth import simulate_round_trip


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_ask: float
    sell_bid: float
    gross_pct: float
    trading_fee_pct: float
    withdrawal_cost_pct: float
    slippage_reserve_pct: float
    net_pct: float
    stale_ms: int
    executable_notional_usdt: float
    transfer_required: bool
    network: Optional[str]
    depth_validated: bool = False
    average_buy: Optional[float] = None
    average_sell: Optional[float] = None


class OpportunityEngine:
    def __init__(self, cfg: dict):
        self.max_age_ms = int(cfg.get("realtime_max_age_ms", 1500))
        self.min_net_pct = float(cfg.get("realtime_min_net_pct", 0.50))
        self.slippage_pct = float(cfg.get("realtime_slippage_reserve_pct", 0.20))
        self.notional_usdt = float(cfg.get("realtime_notional_usdt", 300.0))
        self.fees = {k.lower(): float(v) for k, v in (cfg.get("spot_taker_fee_pct") or {}).items()}
        self.routes = cfg.get("transfer_routes") or {}

    def _route(self, asset: str, buy_ex: str, sell_ex: str):
        return self.routes.get(f"{buy_ex}->{sell_ex}:{asset.upper()}")

    def _costs(self, buy: BBO, sell: BBO):
        buy_fee = self.fees.get(buy.exchange)
        sell_fee = self.fees.get(sell.exchange)
        if buy_fee is None or sell_fee is None:
            return None
        asset = buy.symbol[:-4] if buy.symbol.endswith("USDT") else buy.symbol
        route = self._route(asset, buy.exchange, sell.exchange)
        transfer_required = bool(route and route.get("required"))
        withdrawal_pct = 0.0
        network = None
        if transfer_required:
            if route.get("enabled") is not True:
                return None
            networks = route.get("compatible_networks") or []
            if not networks or route.get("deposit_enabled") is not True or route.get("withdrawal_enabled") is not True:
                return None
            network = networks[0]
            withdrawal_pct = float(route.get("withdrawal_fee_pct", 0.0))
        return buy_fee + sell_fee, withdrawal_pct, transfer_required, network

    def evaluate(self, buy: BBO, sell: BBO, now_ms: Optional[int] = None) -> Optional[Opportunity]:
        if buy.exchange == sell.exchange or buy.symbol != sell.symbol:
            return None
        now_ms = now_ms or int(time.time() * 1000)
        stale = max(now_ms - buy.ts_ms, now_ms - sell.ts_ms)
        if stale < 0 or stale > self.max_age_ms or buy.ask <= 0 or sell.bid <= buy.ask:
            return None
        executable = min(buy.ask * max(buy.ask_qty, 0.0), sell.bid * max(sell.bid_qty, 0.0))
        if executable < self.notional_usdt:
            return None
        costs = self._costs(buy, sell)
        if costs is None:
            return None
        trading, withdrawal_pct, transfer_required, network = costs
        gross = (sell.bid / buy.ask - 1.0) * 100.0
        net = gross - trading - withdrawal_pct - self.slippage_pct
        if net < self.min_net_pct:
            return None
        return Opportunity(buy.symbol, buy.exchange, sell.exchange, buy.ask, sell.bid, gross, trading,
                           withdrawal_pct, self.slippage_pct, net, stale, executable,
                           transfer_required, network)

    def evaluate_depth(self, buy: BBO, sell: BBO, asks: Iterable[Sequence[float]],
                       bids: Iterable[Sequence[float]], now_ms: Optional[int] = None) -> Optional[Opportunity]:
        """Validate the same opportunity against complete order-book depth."""
        base = self.evaluate(buy, sell, now_ms)
        if base is None:
            return None
        fill = simulate_round_trip(asks, bids, self.notional_usdt)
        if not fill.complete or fill.spent_quote <= 0 or fill.received_quote <= 0:
            return None
        costs = self._costs(buy, sell)
        if costs is None:
            return None
        trading, withdrawal_pct, transfer_required, network = costs
        gross = (fill.received_quote / fill.spent_quote - 1.0) * 100.0
        net = gross - trading - withdrawal_pct - self.slippage_pct
        if net < self.min_net_pct:
            return None
        return Opportunity(base.symbol, base.buy_exchange, base.sell_exchange, base.buy_ask,
                           base.sell_bid, gross, trading, withdrawal_pct, self.slippage_pct,
                           net, base.stale_ms, fill.spent_quote, transfer_required, network,
                           True, fill.average_buy, fill.average_sell)


def best_opportunities(book: Dict[str, Dict[str, BBO]], engine: OpportunityEngine):
    """Return current cross-exchange SPOT opportunities, ranked by net model."""
    out = []
    for symbol, by_exchange in book.items():
        rows = list(by_exchange.values())
        for buy in rows:
            for sell in rows:
                opp = engine.evaluate(buy, sell)
                if opp:
                    out.append(opp)
    return sorted(out, key=lambda x: x.net_pct, reverse=True)
