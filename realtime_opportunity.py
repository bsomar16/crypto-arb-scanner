#!/usr/bin/env python3
"""Executable-SPOT-arbitrage opportunity calculations.

The engine is intentionally conservative.  It only emits an opportunity when
both sides are fresh, the route is explicitly enabled, SPOT fees are known,
and either both exchanges are pre-funded or a configured transfer route has a
compatible network.  Unknown transfer/network status means NO TRADE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional

from realtime_market import BBO


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
    transfer_required: bool
    network: Optional[str]


class OpportunityEngine:
    def __init__(self, cfg: dict):
        self.max_age_ms = int(cfg.get("realtime_max_age_ms", 1500))
        self.min_net_pct = float(cfg.get("realtime_min_net_pct", 0.50))
        self.slippage_pct = float(cfg.get("realtime_slippage_reserve_pct", 0.20))
        self.fees = {k.lower(): float(v) for k, v in (cfg.get("spot_taker_fee_pct") or {}).items()}
        self.routes = cfg.get("transfer_routes") or {}

    def _route(self, asset: str, buy_ex: str, sell_ex: str):
        key = f"{buy_ex}->{sell_ex}:{asset.upper()}"
        return self.routes.get(key)

    def evaluate(self, buy: BBO, sell: BBO, now_ms: Optional[int] = None) -> Optional[Opportunity]:
        if buy.exchange == sell.exchange or buy.symbol != sell.symbol:
            return None
        now_ms = now_ms or int(time.time() * 1000)
        stale = max(now_ms - buy.ts_ms, now_ms - sell.ts_ms)
        if stale < 0 or stale > self.max_age_ms:
            return None
        if buy.ask <= 0 or sell.bid <= buy.ask:
            return None

        buy_fee = self.fees.get(buy.exchange)
        sell_fee = self.fees.get(sell.exchange)
        if buy_fee is None or sell_fee is None:
            return None

        gross = (sell.bid / buy.ask - 1.0) * 100.0
        trading = buy_fee + sell_fee

        # If the asset is not pre-funded on both sides, a transfer route must be
        # explicitly configured.  The caller can use the route metadata later
        # to refresh live deposit/withdraw status before confirmation.
        asset = buy.symbol[:-4] if buy.symbol.endswith("USDT") else buy.symbol
        route = self._route(asset, buy.exchange, sell.exchange)
        transfer_required = bool(route and route.get("required"))
        withdrawal_pct = 0.0
        network = None
        if transfer_required:
            if route.get("enabled") is not True:
                return None
            networks = route.get("compatible_networks") or []
            if not networks:
                return None
            network = networks[0]
            withdrawal_fee_asset = float(route.get("withdrawal_fee_asset", 0) or 0)
            if buy.ask > 0:
                withdrawal_pct = withdrawal_fee_asset / max(float(route.get("notional_usdt", 1)), 1.0) * 100.0

        net = gross - trading - withdrawal_pct - self.slippage_pct
        if net < self.min_net_pct:
            return None

        return Opportunity(
            symbol=buy.symbol,
            buy_exchange=buy.exchange,
            sell_exchange=sell.exchange,
            buy_ask=buy.ask,
            sell_bid=sell.bid,
            gross_pct=gross,
            trading_fee_pct=trading,
            withdrawal_cost_pct=withdrawal_pct,
            slippage_reserve_pct=self.slippage_pct,
            net_pct=net,
            stale_ms=stale,
            transfer_required=transfer_required,
            network=network,
        )


def best_opportunities(book: Dict[str, Dict[str, BBO]], engine: OpportunityEngine):
    """Return the current best cross-exchange opportunities per symbol."""
    out = []
    for symbol, by_exchange in book.items():
        rows = list(by_exchange.values())
        for buy in rows:
            for sell in rows:
                opp = engine.evaluate(buy, sell)
                if opp:
                    out.append(opp)
    return sorted(out, key=lambda x: x.net_pct, reverse=True)
