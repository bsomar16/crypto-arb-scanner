#!/usr/bin/env python3
"""Controlled SPOT execution state machine.

Live execution is opt-in. Every order requires explicit confirmation and live
execution also requires a fresh revalidation callback immediately before the
adapter boundary. No derivatives are exposed.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from execution_guard import ExecutionRequest, validate_spot_request, validate_execution_order, validate_withdrawal_request
from execution_recovery import ACTIVE, ExecutionSafety, recover_active_intents


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
    last_revalidated_ms: Optional[int] = None
    idempotency_key: str = ""
    withdrawal_confirmed: bool = False
    error: Optional[str] = None


class ExecutionEngine:
    def __init__(self, cfg: dict, state_dir: str = "state"):
        self.cfg = cfg
        self.enabled = os.getenv("EXECUTION_ENABLED", "false").lower() == "true"
        self.confirm_ttl_ms = int(cfg.get("execution_confirmation_ttl_ms", 30000))
        self.max_notional = float(cfg.get("execution_max_notional_usdt", 300.0))
        self.state = Path(state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.safety = ExecutionSafety(cfg, state_dir)
        self._intents = recover_active_intents(self.state / "execution_intents.jsonl")

    def create_intent(self, opportunity: Any) -> ExecutionIntent:
        if opportunity.net_pct < float(self.cfg.get("realtime_min_net_pct", 0.5)):
            raise ValueError("opportunity below execution threshold")
        notional = float(opportunity.executable_notional_usdt)
        if notional <= 0:
            raise ValueError("opportunity has no executable notional")
        if notional > self.max_notional:
            raise ValueError("opportunity notional exceeds execution limit")
        idem = self._idempotency_key(opportunity)
        existing = self._find_by_idempotency(idem)
        if existing is not None:
            return existing
        self.safety.assert_allowed(notional, len(self._intents), self._daily_notional())
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
            idempotency_key=idem,
        )
        self._intents[intent.id] = asdict(intent)
        self._write(intent)
        return intent

    def confirm(self, intent: ExecutionIntent, explicit_confirmation: bool,
                revalidator: Optional[Callable[[ExecutionIntent], bool]] = None) -> ExecutionIntent:
        now = int(time.time() * 1000)
        if not explicit_confirmation:
            raise PermissionError("explicit confirmation is required")
        if intent.status != "PENDING_CONFIRMATION":
            raise ValueError(f"intent is not awaiting confirmation: {intent.status}")
        if now - intent.created_ms > self.confirm_ttl_ms:
            return self._fail(intent, "confirmation expired", "EXPIRED", TimeoutError)
        self.safety.assert_allowed(intent.notional_usdt, max(0, len(self._intents) - 1), self._daily_notional(exclude=intent.id))
        if self.enabled and revalidator is None:
            raise PermissionError("live execution requires a fresh revalidation callback")
        if revalidator is not None and not revalidator(intent):
            return self._fail(intent, "opportunity revalidation failed", "FAILED", ValueError)
        intent.last_revalidated_ms = now
        intent.status = "READY_FOR_ADAPTER" if self.enabled else "DRY_RUN_CONFIRMED"
        intent.confirmed_ms = now
        self._intents[intent.id] = asdict(intent)
        self._write(intent)
        return intent

    def confirm_withdrawal(self, intent: ExecutionIntent, explicit_confirmation: bool) -> ExecutionIntent:\n        if not explicit_confirmation:\n            raise PermissionError("explicit withdrawal confirmation is required")\n        if intent.status not in {"READY_FOR_ADAPTER", "BUY_SUBMITTED", "BUY_PARTIAL", "BUY_FILLED"}:\n            raise ValueError(f"withdrawal confirmation is invalid for state: {intent.status}")\n        intent.withdrawal_confirmed = True\n        self._intents[intent.id] = asdict(intent)\n        self._write(intent)\n        return intent\n\n    def revalidate_before_adapter(self, intent: ExecutionIntent,
                                  revalidator: Callable[[ExecutionIntent], bool]) -> ExecutionIntent:
        if not self.enabled:
            raise PermissionError("live execution is disabled")
        if intent.status not in ACTIVE - {"PENDING_CONFIRMATION", "DRY_RUN_CONFIRMED"}:
            raise ValueError(f"intent is not in an executable state: {intent.status}")
        if not revalidator(intent):
            return self._fail(intent, "final execution revalidation failed", "FAILED", ValueError)
        intent.last_revalidated_ms = int(time.time() * 1000)
        self._intents[intent.id] = asdict(intent)
        self._write(intent)
        return intent

    def transition(self, intent: ExecutionIntent, target: str) -> ExecutionIntent:
        self.safety.transition(intent.status, target)
        intent.status = target
        self._intents[intent.id] = asdict(intent)
        self._write(intent)
        return intent

    def sync_two_leg_state(self, intent: ExecutionIntent, target: str) -> ExecutionIntent:
        """Mirror the provider-neutral two-leg state into persistent engine state."""
        if intent.status == target:
            return intent
        return self.transition(intent, target)

    def validate_order(self, exchange: str, symbol: str, quantity: float, side: str, *, confirmed: bool,
                       market_type: str = "SPOT", order_type: str = "LIMIT",
                       price: float | None = None, reference_price: float | None = None,
                       signal_price: float | None = None, market_quality: dict | None = None,
                       fresh: bool = True) -> dict:
        if market_type.upper() != "SPOT":
            raise ValueError("SPOT-only policy: non-SPOT market rejected")
        return validate_execution_order(ExecutionRequest(
            product="SPOT", side=side, symbol=symbol, exchange=exchange,
            quantity=quantity, confirmed=confirmed, order_type=order_type, price=price,
        ), cfg={**self.cfg, "execution_live_enabled": self.enabled}, reference_price=reference_price,
           signal_price=signal_price, market_quality=market_quality, fresh=fresh)

    def validate_withdrawal(self, exchange: str, asset: str, quantity: float, *, confirmed: bool,
                            network: str = "", network_enabled: bool = False,
                            destination_confirmed: bool = False) -> dict:
        if not self.enabled:
            raise PermissionError("live execution is disabled")
        if not self._intents:
            raise PermissionError("no active execution intent is available")
        return validate_withdrawal_request(ExecutionRequest(
            product="SPOT", side="SELL", symbol=asset, exchange=exchange,
            quantity=quantity, confirmed=confirmed, withdrawal_confirmed=confirmed,
        ), cfg=self.cfg, network_enabled=network_enabled,
           destination_confirmed=destination_confirmed, network=network)

    def _idempotency_key(self, opportunity: Any) -> str:
        return "|".join(str(x) for x in (
            opportunity.symbol.upper(), opportunity.buy_exchange.lower(), opportunity.sell_exchange.lower(),
            round(float(opportunity.buy_ask), 12), round(float(opportunity.sell_bid), 12),
            round(float(opportunity.executable_notional_usdt), 8), opportunity.network or "",
        ))

    def _find_by_idempotency(self, key: str) -> Optional[ExecutionIntent]:
        row = next((v for v in self._intents.values() if v.get("idempotency_key") == key and v.get("status") not in {"FAILED", "CANCELLED", "EXPIRED", "COMPLETED"}), None)
        return ExecutionIntent(**row) if row else None

    def _daily_notional(self, exclude: Optional[str] = None) -> float:
        cutoff = int(time.time() * 1000) - 86400000
        total = 0.0
        for row in self._intents.values():
            if row.get("id") == exclude or int(row.get("created_ms", 0)) < cutoff:
                continue
            total += float(row.get("notional_usdt", 0))
        return total

    def _fail(self, intent: ExecutionIntent, message: str, status: str, exc_type):
        intent.error = message
        intent.status = status
        self._intents[intent.id] = asdict(intent)
        self._write(intent)
        raise exc_type(message)

    def _write(self, intent: ExecutionIntent) -> None:
        with (self.state / "execution_intents.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(intent), separators=(",", ":")) + "\n")
