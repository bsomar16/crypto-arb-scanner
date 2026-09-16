#!/usr/bin/env python3
"""Controlled adapter-facing executor for a confirmed two-leg SPOT arbitrage.

The executor is intentionally stepwise: every external action is preceded by
fresh validation, and order/transfer status is reconciled before the next leg.
It never treats withdrawal submission as destination deposit confirmation.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable, Optional

from execution_engine import ExecutionEngine, ExecutionIntent
from execution_monitor import reconcile_order
from transfer_tracker import Transfer, TransferTracker
from two_leg_execution import LegFill, LegState, TransferStatus, TwoLegCoordinator, TwoLegIntent
import trade_journal


class TwoLegExecutor:
    def __init__(self, engine: ExecutionEngine, state_dir: str = "state") -> None:
        self.engine = engine
        self.transfers = TransferTracker(state_dir)

    def _journal_transition(self, execution_intent: ExecutionIntent, before: str, after: str, **fields: Any) -> None:
        if before == after:
            return
        trade_journal.record(
            "execution_transition", execution_intent.id,
            symbol=execution_intent.symbol,
            buy_exchange=execution_intent.buy_exchange,
            sell_exchange=execution_intent.sell_exchange,
            state_from=before, state_to=after,
            mode="live" if self.engine.enabled else "dry_run", **fields,
        )

    def _journal_order(self, execution_intent: ExecutionIntent, leg: str, snapshot: LegFill) -> None:
        trade_journal.record(
            f"{leg}_order", execution_intent.id,
            symbol=execution_intent.symbol,
            exchange=execution_intent.buy_exchange if leg == "buy" else execution_intent.sell_exchange,
            order_id=snapshot.order_id,
            order_status=snapshot.status,
            requested_qty=snapshot.requested_qty,
            filled_qty=snapshot.filled_qty,
            avg_price=snapshot.avg_price,
            fee_quote=snapshot.fee_quote,
            mode="live" if self.engine.enabled else "dry_run",
        )

    def coordinator(self, execution_intent: ExecutionIntent, quantity: float) -> TwoLegCoordinator:
        state_path = self.transfers.path.parent / "two_leg_intents.jsonl"
        restored = self._load_latest(execution_intent.id, state_path)
        intent = restored or TwoLegIntent(
            intent_id=execution_intent.id, symbol=execution_intent.symbol,
            buy_exchange=execution_intent.buy_exchange, sell_exchange=execution_intent.sell_exchange,
            requested_qty=quantity, network=execution_intent.network,
        )

        def persist(item: TwoLegIntent) -> None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with state_path.open("a", encoding="utf-8") as fh:
                row = asdict(item); row["state"] = item.state.value
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

        return TwoLegCoordinator(intent, persist=persist)

    def submit_buy(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any, *,
                   price: Optional[float] = None, order_type: str = "LIMIT",
                   revalidate: Callable[[ExecutionIntent], bool]) -> LegState:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        if execution_intent.status not in {"READY_FOR_ADAPTER", "BUY_SUBMITTED", "BUY_PARTIAL"}:
            raise ValueError(f"execution intent is not ready for buy: {execution_intent.status}")
        if coordinator.intent.state not in {LegState.READY_FOR_ADAPTER, LegState.BUY_SUBMITTED, LegState.BUY_PARTIAL}:
            raise ValueError(f"buy leg is not ready: {coordinator.intent.state.value}")
        self.engine.revalidate_before_adapter(execution_intent, revalidate)
        coordinator.revalidate_buy = lambda _: revalidate(execution_intent)
        before = coordinator.intent.state.value
        coordinator.prepare_buy()
        client_id = "arb-" + execution_intent.id[:24]
        raw = adapter.place_spot_order(execution_intent.symbol, "BUY", coordinator.intent.requested_qty,
                                       price=price or execution_intent.buy_price, order_type=order_type,
                                       client_order_id=client_id)
        order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or client_id)
        status = str(raw.get("status") or "NEW").upper()
        executed = float(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0))) or 0)
        fill = LegFill(order_id, status, coordinator.intent.requested_qty, executed,
                       float(raw.get("price", 0) or 0), float(raw.get("fee", raw.get("feeQuote", 0)) or 0))
        state = coordinator.accept_buy(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="buy", order_id=order_id)
        self._journal_order(execution_intent, "buy", fill)
        return state

    def reconcile_buy(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any) -> LegState:
        if not coordinator.intent.buy_order_id:
            raise ValueError("buy order id is missing")
        before = coordinator.intent.state.value
        snap = reconcile_order(adapter, execution_intent.symbol, coordinator.intent.buy_order_id)
        fill = LegFill(coordinator.intent.buy_order_id, snap.status, coordinator.intent.requested_qty,
                       snap.executed_qty, snap.avg_price)
        state = coordinator.accept_buy(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="buy", order_id=coordinator.intent.buy_order_id)
        if before != state.value:
            self._journal_order(execution_intent, "buy", fill)
        return state

    def submit_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator,
                        source_adapter: Any, destination_adapter: Any, asset: str, *,
                        revalidate: Callable[[ExecutionIntent], bool]) -> LegState:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        self.engine.revalidate_before_adapter(execution_intent, revalidate)
        coordinator.revalidate_transfer = lambda _: revalidate(execution_intent)
        before = coordinator.intent.state.value
        state = coordinator.begin_transfer()
        if state != LegState.TRANSFER_PENDING:
            self.engine.sync_two_leg_state(execution_intent, state.value)
            self._journal_transition(execution_intent, before, state.value, leg="transfer")
            return state
        network = execution_intent.network
        if not network:
            return self._fail_transfer(execution_intent, coordinator, "no validated transfer network")
        address = destination_adapter.get_deposit_address(asset, network)
        transfer_id = "tr-" + uuid.uuid4().hex
        raw = source_adapter.withdraw_spot(asset, coordinator.intent.filled_qty, address, network,
                                           client_withdrawal_id=transfer_id)
        provider_id = str(raw.get("id") or raw.get("withdrawalId") or raw.get("txId") or transfer_id)
        now = int(time.time() * 1000)
        self.transfers.create(Transfer(
            id=provider_id, asset=asset.upper(), amount=coordinator.intent.filled_qty,
            source_exchange=execution_intent.buy_exchange, destination_exchange=execution_intent.sell_exchange,
            network=network, status="SUBMITTED",
            txid=str(raw.get("txId") or raw.get("txid") or "") or None,
            created_ms=now, updated_ms=now,
        ))
        coordinator.accept_transfer(TransferStatus(provider_id, "SUBMITTED", coordinator.intent.filled_qty))
        self.engine.sync_two_leg_state(execution_intent, coordinator.intent.state.value)
        self._journal_transition(execution_intent, before, coordinator.intent.state.value, leg="transfer", transfer_id=provider_id)
        trade_journal.record("transfer_submitted", execution_intent.id, asset=asset.upper(), amount=coordinator.intent.filled_qty,
                             source_exchange=execution_intent.buy_exchange, destination_exchange=execution_intent.sell_exchange,
                             network=network, transfer_id=provider_id, mode="live")
        return coordinator.intent.state

    def confirm_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, transfer_id: str, *,
                         destination_balance_confirmed: bool, status: str = "CONFIRMED",
                         tx_hash: Optional[str] = None) -> LegState:
        transfer = self.transfers.transfers.get(transfer_id)
        if transfer is None:
            raise ValueError("unknown transfer id")
        if status.upper() in {"CONFIRMED", "COMPLETED"} and not destination_balance_confirmed:
            raise ValueError("destination balance confirmation is required")
        if status.upper() in {"CONFIRMED", "COMPLETED"}:
            if transfer.status in {"SUBMITTED", "CONFIRMING"}:
                self.transfers.transition(transfer_id, "COMPLETED", txid=tx_hash)
        before = coordinator.intent.state.value
        state = coordinator.accept_transfer(TransferStatus(transfer_id, status, transfer.amount, tx_hash, destination_balance_confirmed))
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="transfer", transfer_id=transfer_id, tx_hash=tx_hash)
        return state

    def submit_sell(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any, *,
                    price: Optional[float] = None, order_type: str = "LIMIT",
                    revalidate: Callable[[ExecutionIntent], bool]) -> LegState:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        self.engine.revalidate_before_adapter(execution_intent, revalidate)
        coordinator.revalidate_sell = lambda _: revalidate(execution_intent)
        before = coordinator.intent.state.value
        coordinator.prepare_sell()
        client_id = "arb-" + execution_intent.id[:24] + "-s"
        raw = adapter.place_spot_order(execution_intent.symbol, "SELL", coordinator.intent.transferred_qty,
                                       price=price or execution_intent.sell_price, order_type=order_type,
                                       client_order_id=client_id)
        order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or client_id)
        status = str(raw.get("status") or "NEW").upper()
        executed = float(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0))) or 0)
        fill = LegFill(order_id, status, coordinator.intent.transferred_qty, executed,
                       float(raw.get("price", 0) or 0), float(raw.get("fee", raw.get("feeQuote", 0)) or 0))
        state = coordinator.accept_sell(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="sell", order_id=order_id)
        self._journal_order(execution_intent, "sell", fill)
        return state

    def reconcile_sell(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any) -> LegState:
        if not coordinator.intent.sell_order_id:
            raise ValueError("sell order id is missing")
        before = coordinator.intent.state.value
        snap = reconcile_order(adapter, execution_intent.symbol, coordinator.intent.sell_order_id)
        fill = LegFill(coordinator.intent.sell_order_id, snap.status, coordinator.intent.transferred_qty,
                       snap.executed_qty, snap.avg_price)
        state = coordinator.accept_sell(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="sell", order_id=coordinator.intent.sell_order_id)
        if before != state.value:
            self._journal_order(execution_intent, "sell", fill)
        return state

    def _fail_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, reason: str) -> LegState:
        before = coordinator.intent.state.value
        state = coordinator._fail(reason)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="transfer", error=reason)
        return state

    @staticmethod
    def _load_latest(intent_id: str, path) -> Optional[TwoLegIntent]:
        if not path.exists():
            return None
        latest = None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("intent_id") != intent_id:
                    continue
                row["state"] = LegState(str(row.get("state", "READY_FOR_ADAPTER")))
                latest = TwoLegIntent(**{k: v for k, v in row.items() if k in TwoLegIntent.__dataclass_fields__})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return latest
