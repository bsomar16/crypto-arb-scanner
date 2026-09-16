#!/usr/bin/env python3
"""Controlled SPOT execution state machine.

Live execution is opt-in. The engine refuses derivative-style order types and
requires a fresh opportunity plus explicit confirmation for every execution.
Withdrawal is always a separate confirmation.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

from execution_guard import SpotOnlyGuard


@dataclass
class ExecutionIntent:
    id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    notional_usdt: float
    buy_price: float
    sell_price: float
    net_pct: float
    transfer_required: bool
    network: Optional[str]
    status: str = "PENDING_CONFIRMATION"
    created_ms: int = 0
    confirmed_ms: Optional[int] = None


class ExecutionEngine:
    def __init__(self, cfg: dict, state_dir: str = "state"):
        self.cfg = cfg
        self.guard = SpotOnlyGuard()
        self.enabled = os.getenv("EXECUTION_ENABLED", "false").lower() == "true"
        self.confirm_ttl_ms = int(cfg.get("execution_confirmation_ttl_ms", 30000))
        self.max_notional = float(cfg.get("execution_max_notional_usdt", 300.0))
        self.state = Path(state_dir)
        self.state.mkdir(parents=True, exist_ok=True)

    def create_intent(self, opportunity: Any) -> ExecutionIntent:
        if opportunity.net_pct < float(self.cfg.get("realtime_min_net_pct", 0.5)):
            raise ValueError("opportunity below execution threshold")
        notional = min(float(opportunity.executable_notional_usdt), self.max_notional)
        intent = ExecutionIntent(
            id=uuid.uuid4().hex,
            symbol=opportunity.symbol,
            buy_exchange=opportunity.buy_exchange,
            sell_exchange=opportunity.sell_exchange,
            notional_usdt=notional,
            buy_price=opportunity.buy_ask,
            sell_price=opportunity.sell_bid,
            net_pct=opportunity.net_pct,
            transfer_required=opportunity.transfer_required,
            network=opportunity.network,
            created_ms=int(time.time() * 1000),
        )
        self._write(intent)
        return intent

    def confirm(self, intent: ExecutionIntent, explicit_confirmation: bool) -> ExecutionIntent:
        now = int(time.time() * 1000)
        if not explicit_confirmation:
            raise PermissionError("explicit confirmation is required")
        if now - intent.created_ms > self.confirm_ttl_ms:
            intent.status = "EXPIRED"
            self._write(intent)
            raise TimeoutError("execution confirmation expired")
        if not self.enabled:
            intent.status = "DRY_RUN_CONFIRMED"
        else:
            intent.status = "READY_FOR_ADAPTER"
        intent.confirmed_ms = now
        self._write(intent)
        return intent

    def validate_order(self, side: str, market_type: str = "SPOT", order_type: str = "LIMIT") -> None:
        self.guard.assert_spot_only(market_type=market_type, side=side, order_type=order_type)

    def _write(self, intent: ExecutionIntent) -> None:
        p = self.state / "execution_intents.jsonl"
        with p.open("a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(asdict(intent), separators=(",", ":")) + "\n")
