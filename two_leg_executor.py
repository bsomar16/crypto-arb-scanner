#!/usr/bin/env python3
"""Controlled adapter-facing executor for a confirmed two-leg SPOT arbitrage."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable, Optional

from execution_engine import ExecutionEngine, ExecutionIntent
from execution_monitor import reconcile_order
from order_constraints import OrderConstraintError, normalize_spot_order
from transfer_tracker import Transfer, TransferTracker
from transfer_reconciliation import ReconciliationResult, reconcile_transfer
from two_leg_execution import LegFill, LegState, TransferStatus, TwoLegCoordinator, TwoLegIntent
import trade_journal


class TwoLegExecutor:
    def __init__(self, engine: ExecutionEngine, state_dir: str = "state") -> None:
        self.engine = engine
        self.transfers = TransferTracker(state_dir)

    def _journal_transition(self, execution_intent: ExecutionIntent, before: str, after: str, **fields: Any) -> None:
        if before == after:
            return
        trade_journal.record("execution_transition", execution_intent.id,
                             symbol=execution_intent.symbol, buy_exchange=execution_intent.buy_exchange,
                             sell_exchange=execution_intent.sell_exchange, state_from=before, state_to=after,
                             mode="live" if self.engine.enabled else "dry_run", **fields)

    def _journal_order(self, execution_intent: ExecutionIntent, leg: str, snapshot: LegFill) -> None:
        trade_journal.record(f"{leg}_order", execution_intent.id,
                             symbol=execution_intent.symbol,
                             exchange=execution_intent.buy_exchange if leg == "buy" else execution_intent.sell_exchange,
                             order_id=snapshot.order_id, order_status=snapshot.status,
                             requested_qty=snapshot.requested_qty, filled_qty=snapshot.filled_qty,
                             executed_qty=snapshot.filled_qty, avg_price=snapshot.avg_price,
                             fee_quote=snapshot.fee_quote, fee_amount=snapshot.fee_amount,
                             fee_currency=snapshot.fee_currency, mode="live" if self.engine.enabled else "dry_run")

    @staticmethod
    def _spot_market(adapter: Any, symbol: str) -> Any:
        target = symbol.upper()
        for market in adapter.get_spot_markets():
            if str(market.symbol).upper() == target:
                return market
        raise RuntimeError(f"SPOT market metadata not found for {adapter.name}/{symbol}")

    def _normalize_buy_order(self, adapter: Any, symbol: str, quantity: float, price: Optional[float]) -> tuple[float, Optional[float]]:
        market = self._spot_market(adapter, symbol)
        try:
            normalized = normalize_spot_order(market, quantity, price)
        except OrderConstraintError as exc:
            raise RuntimeError(f"buy order violates SPOT market constraints: {exc}") from exc
        return normalized.quantity, normalized.price

    def _normalize_sell_order(self, adapter: Any, symbol: str, quantity: float, price: Optional[float]) -> tuple[float, Optional[float]]:
        market = self._spot_market(adapter, symbol)
        try:
            normalized = normalize_spot_order(market, quantity, price)
        except OrderConstraintError as exc:
            raise RuntimeError(f"sell order violates SPOT market constraints: {exc}") from exc
        if normalized.quantity + 1e-12 < quantity:
            raise RuntimeError(
                f"sell quantity {quantity} is not aligned to {adapter.name} SPOT step {market.qty_step}; "
                "refusing to create unsold dust"
            )
        return normalized.quantity, normalized.price

    def coordinator(self, execution_intent: ExecutionIntent, quantity: float) -> TwoLegCoordinator:
        state_path = self.transfers.path.parent / "two_leg_intents.jsonl"
        restored = self._load_latest(execution_intent.id, state_path)
        intent = restored or TwoLegIntent(intent_id=execution_intent.id, symbol=execution_intent.symbol,
            buy_exchange=execution_intent.buy_exchange, sell_exchange=execution_intent.sell_exchange,
            requested_qty=quantity, network=execution_intent.network)
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
        normalized_qty, normalized_price = self._normalize_buy_order(
            adapter, execution_intent.symbol, coordinator.intent.requested_qty,
            price if price is not None else execution_intent.buy_price,
        )
        coordinator.intent.requested_qty = normalized_qty
        self.engine.validate_order(
            execution_intent.buy_exchange,
            execution_intent.symbol,
            normalized_qty,
            "BUY",
            confirmed=True,
            order_type=order_type,
            price=normalized_price,
            reference_price=normalized_price,
            signal_price=execution_intent.buy_price,
        )
        coordinator.prepare_buy()
        client_id = "arb-" + execution_intent.id[:24]
        raw = adapter.place_spot_order(execution_intent.symbol, "BUY", normalized_qty,
                                       price=normalized_price, order_type=order_type,
                                       client_order_id=client_id)
        order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or client_id)
        status = str(raw.get("status") or "NEW").upper()
        executed = float(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0))) or 0)
        fill = LegFill(order_id, status, coordinator.intent.requested_qty, executed,
                       float(raw.get("avgPrice", raw.get("price", 0)) or 0),
                       float(raw.get("fee", raw.get("feeQuote", 0)) or 0),
                       float(raw.get("feeAmount", 0) or 0), str(raw.get("feeCurrency") or raw.get("feeCcy") or ""))
        state = coordinator.accept_buy(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="buy", order_id=order_id,
                                 normalized_qty=normalized_qty, normalized_price=normalized_price)
        self._journal_order(execution_intent, "buy", fill)
        return state

    def reconcile_buy(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any) -> LegState:
        if not coordinator.intent.buy_order_id:
            raise ValueError("buy order id is missing")
        before = coordinator.intent.state.value
        snap = reconcile_order(adapter, execution_intent.symbol, coordinator.intent.buy_order_id)
        if before == LegState.BUY_FILLED.value and snap.status == "FILLED":
            return coordinator.intent.state
        fill = LegFill(coordinator.intent.buy_order_id, snap.status, coordinator.intent.requested_qty,
                       snap.executed_qty, snap.avg_price, 0.0, snap.fee_amount, snap.fee_currency)
        state = coordinator.accept_buy(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="buy", order_id=coordinator.intent.buy_order_id)
        if before != state.value:
            self._journal_order(execution_intent, "buy", fill)
        return state

    @staticmethod
    def _network(networks: list[Any], network: str) -> Optional[Any]:
        target = network.strip().upper()
        for item in networks:
            name = str(getattr(item, "network", "")).strip().upper()
            raw = str(getattr(item, "raw_chain", "")).strip().upper()
            if target == name or (raw and target == raw):
                return item
        return None

    def _validate_transfer_route(self, execution_intent: ExecutionIntent, source_adapter: Any,
                                 destination_adapter: Any, asset: str, network: str,
                                 amount: float) -> None:
        source_network = self._network(source_adapter.get_networks(asset), network)
        if source_network is None:
            raise RuntimeError(f"source exchange does not expose validated network {asset}/{network}")
        if not bool(getattr(source_network, "withdrawal_enabled", False)):
            raise RuntimeError(f"source withdrawal is disabled for {asset}/{network}")
        if amount < float(getattr(source_network, "min_withdrawal", 0) or 0):
            raise RuntimeError(f"transfer amount is below source minimum withdrawal for {asset}/{network}")
        destination_network = self._network(destination_adapter.get_networks(asset), network)
        if destination_network is None:
            raise RuntimeError(f"destination exchange does not expose validated network {asset}/{network}")
        if not bool(getattr(destination_network, "deposit_enabled", False)):
            raise RuntimeError(f"destination deposit is disabled for {asset}/{network}")
        balances = source_adapter.get_spot_balances()
        available = float(balances.get(asset.upper(), 0) or 0)
        fee = max(0.0, float(getattr(source_network, "withdrawal_fee", 0) or 0))
        if available + 1e-12 < amount + fee:
            raise RuntimeError(f"insufficient source balance for transfer plus withdrawal fee: available={available}, required={amount + fee}")

    def _transferable_buy_qty(self, coordinator: TwoLegCoordinator, asset: str) -> float:
        """Reduce the transferable base quantity only when the actual fill fee is in base."""
        amount = coordinator.intent.filled_qty
        if coordinator.intent.buy_fee_currency.upper() == asset.upper():
            amount -= coordinator.intent.buy_fee_amount
        if amount <= 0:
            raise RuntimeError("actual buy fill leaves no transferable base quantity after fees")
        return amount

    def submit_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator,
                        source_adapter: Any, destination_adapter: Any, asset: str, *,
                        revalidate: Callable[[ExecutionIntent], bool]) -> LegState:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        self.engine.revalidate_before_adapter(execution_intent, revalidate)
        coordinator.revalidate_transfer = lambda _: revalidate(execution_intent)
        before = coordinator.intent.state.value
        if not execution_intent.withdrawal_confirmed:
            raise PermissionError("separate withdrawal confirmation is required before transfer")
        transfer_amount = self._transferable_buy_qty(coordinator, asset)
        state = coordinator.begin_transfer(transfer_amount)
        if state != LegState.TRANSFER_PENDING:
            self.engine.sync_two_leg_state(execution_intent, state.value)
            self._journal_transition(execution_intent, before, state.value, leg="transfer")
            return state
        network = execution_intent.network
        if not network:
            return self._fail_transfer(execution_intent, coordinator, "no validated transfer network")
        self._validate_transfer_route(execution_intent, source_adapter, destination_adapter,
                                      asset, network, coordinator.intent.transferred_qty)
        source_network = self._network(source_adapter.get_networks(asset), network)
        self.engine.validate_withdrawal(
            execution_intent.buy_exchange, asset, coordinator.intent.transferred_qty,
            confirmed=True, network=network,
            network_enabled=bool(getattr(source_network, "withdrawal_enabled", False)),
            destination_confirmed=True,
        )
        details = destination_adapter.get_deposit_details(asset, network)
        address = str(details.get("address", ""))
        memo = str(details.get("memo") or "")
        memo_type = str(details.get("memo_type") or "")
        destination_network = self._network(destination_adapter.get_networks(asset), network)
        destination_requires_memo = bool(getattr(destination_network, "memo_required", False))
        if not address:
            return self._fail_transfer(execution_intent, coordinator, "destination did not return a deposit address")
        if destination_requires_memo and not memo:
            self._fail_transfer(execution_intent, coordinator, "destination requires memo/tag but did not return one")
            raise RuntimeError("destination requires memo/tag but did not return one")
        transfer_id = "tr-" + uuid.uuid4().hex
        raw = source_adapter.withdraw_spot(asset, coordinator.intent.transferred_qty, address, network,
                                           memo=memo or None, memo_type=memo_type or None,
                                           client_withdrawal_id=transfer_id)
        provider_id = str(raw.get("id") or raw.get("withdrawalId") or raw.get("txId") or transfer_id)
        now = int(time.time() * 1000)
        self.transfers.create(Transfer(id=provider_id, asset=asset.upper(), amount=coordinator.intent.transferred_qty,
            source_exchange=execution_intent.buy_exchange, destination_exchange=execution_intent.sell_exchange,
            network=network, status="SUBMITTED", txid=str(raw.get("txId") or raw.get("txid") or "") or None,
            created_ms=now, updated_ms=now))
        coordinator.accept_transfer(TransferStatus(provider_id, "SUBMITTED", coordinator.intent.transferred_qty))
        self.engine.sync_two_leg_state(execution_intent, coordinator.intent.state.value)
        self._journal_transition(execution_intent, before, coordinator.intent.state.value, leg="transfer", transfer_id=provider_id, memo_type=memo_type,
                                 amount=coordinator.intent.transferred_qty, buy_fee_currency=coordinator.intent.buy_fee_currency,
                                 buy_fee_amount=coordinator.intent.buy_fee_amount)
        trade_journal.record("transfer_submitted", execution_intent.id, asset=asset.upper(), amount=coordinator.intent.transferred_qty,
                             source_exchange=execution_intent.buy_exchange, destination_exchange=execution_intent.sell_exchange,
                             network=network, transfer_id=provider_id, memo_type=memo_type, mode="live")
        return coordinator.intent.state

    def reconcile_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator,
                           source_adapter: Any, destination_adapter: Any, asset: str, transfer_id: str,
                           *, expected_address: str = "", created_ms: int = 0) -> tuple[LegState, ReconciliationResult]:
        """Poll both exchanges and release SELL only after independent credit confirmation."""
        transfer = self.transfers.transfers.get(transfer_id)
        if transfer is None:
            raise ValueError("unknown transfer id")
        result = reconcile_transfer(
            source_adapter, destination_adapter,
            transfer_id=transfer_id,
            asset=asset,
            network=transfer.network,
            expected_amount=transfer.amount,
            expected_address=expected_address,
            created_ms=created_ms or transfer.created_ms,
        )
        if result.status == "COMPLETED":
            tx_hash = result.source.tx_hash or result.destination.tx_hash or transfer.txid
            self.transfers.transition(transfer_id, "COMPLETED", txid=tx_hash or None)
            state = self.confirm_transfer(
                execution_intent, coordinator, transfer_id,
                destination_balance_confirmed=True,
                status="CONFIRMED", tx_hash=tx_hash,
                reconciliation=result,
            )
        elif result.status == "FAILED":
            state = self.confirm_transfer(
                execution_intent, coordinator, transfer_id,
                destination_balance_confirmed=False,
                status="FAILED", tx_hash=result.source.tx_hash or result.destination.tx_hash or transfer.txid,
                reconciliation=result,
            )
        else:
            state = coordinator.accept_transfer(TransferStatus(
                transfer_id, "CONFIRMING", transfer.amount,
                result.source.tx_hash or result.destination.tx_hash or transfer.txid,
                False,
            ))
            self.engine.sync_two_leg_state(execution_intent, state.value)
        trade_journal.record("transfer_reconciled", execution_intent.id,
                             transfer_id=transfer_id, status=result.status, reason=result.reason,
                             source_status=result.source.status if result.source else "",
                             destination_status=result.destination.status if result.destination else "",
                             tx_hash=(result.source.tx_hash or result.destination.tx_hash) if result.source or result.destination else "",
                             mode="live" if self.engine.enabled else "dry_run")
        return state, result

    def confirm_transfer(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, transfer_id: str, *,
                         destination_balance_confirmed: bool, status: str = "CONFIRMED",
                         tx_hash: Optional[str] = None,
                         reconciliation: Optional[ReconciliationResult] = None) -> LegState:
        transfer = self.transfers.transfers.get(transfer_id)
        if transfer is None:
            raise ValueError("unknown transfer id")
        normalized_status = status.upper()
        if normalized_status in {"CONFIRMED", "COMPLETED"}:
            if reconciliation is None or reconciliation.status != "COMPLETED":
                raise ValueError("independent transfer reconciliation is required before SELL")
            if not destination_balance_confirmed:
                raise ValueError("destination balance confirmation is required")
            if reconciliation.destination is None or reconciliation.source is None:
                raise ValueError("source and destination reconciliation snapshots are required")
            if reconciliation.destination.amount + 1e-12 < transfer.amount:
                raise ValueError("reconciled destination amount does not cover intended transfer")
            if reconciliation.source.status != "COMPLETED" or reconciliation.destination.status != "COMPLETED":
                raise ValueError("source withdrawal and destination deposit are not independently completed")
            tx_hash = tx_hash or reconciliation.source.tx_hash or reconciliation.destination.tx_hash
            if not tx_hash:
                raise ValueError("completed on-chain transfer requires a transaction hash")
        if normalized_status in {"CONFIRMED", "COMPLETED"} and transfer.status in {"SUBMITTED", "CONFIRMING"}:
            self.transfers.transition(transfer_id, "COMPLETED", txid=tx_hash)
        before = coordinator.intent.state.value
        state = coordinator.accept_transfer(TransferStatus(transfer_id, normalized_status, transfer.amount, tx_hash, destination_balance_confirmed))
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="transfer", transfer_id=transfer_id, tx_hash=tx_hash,
                                 reconciliation_status=reconciliation.status if reconciliation else "")
        return state

    def submit_sell(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any, *,
                    price: Optional[float] = None, order_type: str = "LIMIT",
                    revalidate: Callable[[ExecutionIntent], bool]) -> LegState:
        if not self.engine.enabled:
            raise PermissionError("live execution is disabled")
        self.engine.revalidate_before_adapter(execution_intent, revalidate)
        coordinator.revalidate_sell = lambda _: revalidate(execution_intent)
        before = coordinator.intent.state.value
        normalized_qty, normalized_price = self._normalize_sell_order(
            adapter, execution_intent.symbol, coordinator.intent.transferred_qty,
            price if price is not None else execution_intent.sell_price,
        )
        self.engine.validate_order(
            execution_intent.sell_exchange,
            execution_intent.symbol,
            normalized_qty,
            "SELL",
            confirmed=True,
            order_type=order_type,
            price=normalized_price,
            reference_price=normalized_price,
            signal_price=execution_intent.sell_price,
        )
        coordinator.prepare_sell()
        client_id = "arb-" + execution_intent.id[:24] + "-s"
        raw = adapter.place_spot_order(execution_intent.symbol, "SELL", normalized_qty,
                                       price=normalized_price, order_type=order_type,
                                       client_order_id=client_id)
        order_id = str(raw.get("orderId") or raw.get("order_id") or raw.get("id") or client_id)
        status = str(raw.get("status") or "NEW").upper()
        executed = float(raw.get("executedQty", raw.get("cumExecQty", raw.get("filledQty", 0))) or 0)
        fill = LegFill(order_id, status, coordinator.intent.transferred_qty, executed,
                       float(raw.get("avgPrice", raw.get("price", 0)) or 0),
                       float(raw.get("fee", raw.get("feeQuote", 0)) or 0),
                       float(raw.get("feeAmount", 0) or 0), str(raw.get("feeCurrency") or raw.get("feeCcy") or ""))
        state = coordinator.accept_sell(fill)
        self.engine.sync_two_leg_state(execution_intent, state.value)
        self._journal_transition(execution_intent, before, state.value, leg="sell", order_id=order_id,
                                 normalized_qty=normalized_qty, normalized_price=normalized_price)
        self._journal_order(execution_intent, "sell", fill)
        return state

    def reconcile_sell(self, execution_intent: ExecutionIntent, coordinator: TwoLegCoordinator, adapter: Any) -> LegState:
        if not coordinator.intent.sell_order_id:
            raise ValueError("sell order id is missing")
        before = coordinator.intent.state.value
        snap = reconcile_order(adapter, execution_intent.symbol, coordinator.intent.sell_order_id)
        if before == LegState.SELL_FILLED.value and snap.status == "FILLED":
            return coordinator.intent.state
        fill = LegFill(coordinator.intent.sell_order_id, snap.status, coordinator.intent.transferred_qty,
                       snap.executed_qty, snap.avg_price, 0.0, snap.fee_amount, snap.fee_currency)
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
