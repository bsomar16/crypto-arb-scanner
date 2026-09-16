"""Failure-safe coordinator for a confirmed two-leg SPOT arbitrage intent.

Provider-neutral: exchange I/O is injected through callbacks. The coordinator
never exposes derivatives and never assumes a withdrawal request means a
completed destination deposit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class LegState(str, Enum):
    READY_FOR_ADAPTER = "READY_FOR_ADAPTER"
    BUY_SUBMITTED = "BUY_SUBMITTED"
    BUY_PARTIAL = "BUY_PARTIAL"
    BUY_FILLED = "BUY_FILLED"
    TRANSFER_PENDING = "TRANSFER_PENDING"
    TRANSFER_CONFIRMED = "TRANSFER_CONFIRMED"
    SELL_SUBMITTED = "SELL_SUBMITTED"
    SELL_PARTIAL = "SELL_PARTIAL"
    SELL_FILLED = "SELL_FILLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class LegFill:
    order_id: str
    status: str
    requested_qty: float
    filled_qty: float
    avg_price: float = 0.0
    fee_quote: float = 0.0
    fee_amount: float = 0.0
    fee_currency: str = ""


@dataclass
class TransferStatus:
    transfer_id: str
    status: str
    amount: float
    tx_hash: Optional[str] = None
    destination_balance_confirmed: bool = False


@dataclass
class TwoLegIntent:
    intent_id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    requested_qty: float
    network: Optional[str] = None
    state: LegState = LegState.READY_FOR_ADAPTER
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    transfer_id: Optional[str] = None
    filled_qty: float = 0.0
    transfer_requested_qty: float = 0.0
    transferred_qty: float = 0.0
    sell_filled_qty: float = 0.0
    buy_fee_quote: float = 0.0
    buy_fee_amount: float = 0.0
    buy_fee_currency: str = ""
    sell_fee_quote: float = 0.0
    sell_fee_amount: float = 0.0
    sell_fee_currency: str = ""
    error: Optional[str] = None
    events: list[str] = field(default_factory=list)


class TwoLegCoordinator:
    """Deterministic state machine; all exchange operations are external callbacks."""

    def __init__(self, intent: TwoLegIntent, persist: Optional[Callable[[TwoLegIntent], None]] = None,
                 revalidate_buy: Optional[Callable[[TwoLegIntent], bool]] = None,
                 revalidate_transfer: Optional[Callable[[TwoLegIntent], bool]] = None,
                 revalidate_sell: Optional[Callable[[TwoLegIntent], bool]] = None):
        self.intent = intent
        self.persist = persist
        self.revalidate_buy = revalidate_buy
        self.revalidate_transfer = revalidate_transfer
        self.revalidate_sell = revalidate_sell

    def _save(self, event: str) -> None:
        self.intent.events.append(event)
        if self.persist:
            self.persist(self.intent)

    def _fail(self, reason: str) -> LegState:
        self.intent.error = reason
        self.intent.state = LegState.FAILED
        self._save("FAILED:" + reason)
        return self.intent.state

    def _check(self, callback: Optional[Callable[[TwoLegIntent], bool]], what: str) -> bool:
        if callback is not None and not callback(self.intent):
            self._fail(what + " revalidation failed")
            return False
        return True

    def prepare_buy(self) -> LegState:
        if self.intent.state != LegState.READY_FOR_ADAPTER:
            raise ValueError("buy preparation is invalid for current state")
        if not self._check(self.revalidate_buy, "buy leg"):
            return self.intent.state
        self._save("BUY_REVALIDATED")
        return self.intent.state

    def accept_buy(self, fill: LegFill) -> LegState:
        if self.intent.state not in {LegState.READY_FOR_ADAPTER, LegState.BUY_SUBMITTED, LegState.BUY_PARTIAL}:
            raise ValueError("buy update is invalid for current state")
        if fill.requested_qty <= 0 or fill.filled_qty < 0 or fill.filled_qty > fill.requested_qty + 1e-12:
            return self._fail("invalid buy fill quantity")
        self.intent.buy_order_id = fill.order_id
        self.intent.filled_qty = fill.filled_qty
        self.intent.buy_fee_quote = max(0.0, fill.fee_quote)
        self.intent.buy_fee_amount = max(0.0, fill.fee_amount)
        self.intent.buy_fee_currency = fill.fee_currency.upper()
        status = fill.status.upper()
        if status == "FILLED" and fill.filled_qty > 0:
            self.intent.state = LegState.BUY_FILLED
        elif fill.filled_qty > 0:
            self.intent.state = LegState.BUY_PARTIAL
        elif status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
            return self._fail("buy leg " + status.lower())
        else:
            self.intent.state = LegState.BUY_SUBMITTED
        self._save("BUY:" + status)
        return self.intent.state

    def begin_transfer(self, amount: Optional[float] = None) -> LegState:
        if self.intent.state != LegState.BUY_FILLED:
            raise ValueError("transfer requires a fully filled buy leg")
        if self.intent.filled_qty <= 0:
            return self._fail("buy leg has no filled quantity")
        if not self._check(self.revalidate_transfer, "transfer"):
            return self.intent.state
        transfer_amount = self.intent.filled_qty if amount is None else float(amount)
        if transfer_amount <= 0 or transfer_amount > self.intent.filled_qty + 1e-12:
            return self._fail("invalid transfer amount")
        self.intent.transfer_requested_qty = transfer_amount
        self.intent.transferred_qty = transfer_amount
        self.intent.state = LegState.TRANSFER_PENDING
        self._save("TRANSFER_SUBMITTED")
        return self.intent.state

    def accept_transfer(self, transfer: TransferStatus) -> LegState:
        if self.intent.state != LegState.TRANSFER_PENDING:
            raise ValueError("transfer update is invalid for current state")
        expected = self.intent.transfer_requested_qty or self.intent.filled_qty
        if transfer.amount <= 0 or transfer.amount > expected + 1e-12:
            return self._fail("invalid transfer quantity")
        self.intent.transfer_id = transfer.transfer_id
        self.intent.transferred_qty = transfer.amount
        status = transfer.status.upper()
        if status in {"CONFIRMED", "COMPLETED"}:
            if not transfer.destination_balance_confirmed:
                return self._fail("destination deposit/balance is not confirmed")
            if transfer.amount + 1e-12 < expected:
                return self._fail("confirmed transfer does not cover intended transferred quantity")
            self.intent.state = LegState.TRANSFER_CONFIRMED
        elif status in {"FAILED", "REJECTED", "CANCELED", "CANCELLED"}:
            return self._fail("transfer " + status.lower())
        else:
            self._save("TRANSFER:" + status)
            return self.intent.state
        self._save("TRANSFER:" + status)
        return self.intent.state

    def prepare_sell(self) -> LegState:
        if self.intent.state != LegState.TRANSFER_CONFIRMED:
            raise ValueError("sell preparation requires confirmed destination deposit")
        if self.intent.transferred_qty <= 0:
            return self._fail("no confirmed transferred quantity")
        if not self._check(self.revalidate_sell, "sell leg"):
            return self.intent.state
        self._save("SELL_REVALIDATED")
        return self.intent.state

    def accept_sell(self, fill: LegFill) -> LegState:
        if self.intent.state not in {LegState.TRANSFER_CONFIRMED, LegState.SELL_SUBMITTED, LegState.SELL_PARTIAL}:
            raise ValueError("sell update is invalid for current state")
        max_qty = self.intent.transferred_qty
        if fill.requested_qty <= 0 or fill.filled_qty < 0 or fill.filled_qty > max_qty + 1e-12:
            return self._fail("sell fill exceeds transferred quantity")
        self.intent.sell_order_id = fill.order_id
        self.intent.sell_filled_qty = fill.filled_qty
        self.intent.sell_fee_quote = max(0.0, fill.fee_quote)
        self.intent.sell_fee_amount = max(0.0, fill.fee_amount)
        self.intent.sell_fee_currency = fill.fee_currency.upper()
        status = fill.status.upper()
        if status == "FILLED" and fill.filled_qty > 0:
            if abs(fill.filled_qty - max_qty) > max(1e-12, max_qty * 1e-8):
                return self._fail("sell marked filled before selling the confirmed transferred quantity")
            self.intent.state = LegState.SELL_FILLED
            self._save("SELL:FILLED")
            self.intent.state = LegState.COMPLETED
            self._save("COMPLETED")
            return self.intent.state
        if fill.filled_qty > 0:
            self.intent.state = LegState.SELL_PARTIAL
        elif status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
            return self._fail("sell leg " + status.lower())
        else:
            self.intent.state = LegState.SELL_SUBMITTED
        self._save("SELL:" + status)
        return self.intent.state

    def cancel(self, reason: str = "cancelled by operator") -> LegState:
        if self.intent.state in {LegState.COMPLETED, LegState.FAILED, LegState.CANCELLED, LegState.EXPIRED}:
            return self.intent.state
        self.intent.error = reason
        self.intent.state = LegState.CANCELLED
        self._save("CANCELLED")
        return self.intent.state
