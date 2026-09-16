#!/usr/bin/env python3
"""Provider-neutral authenticated SPOT adapter contract.

Concrete exchange adapters must implement this contract without exposing
credentials to callers. Derivatives are deliberately absent from the API.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SpotMarket:
    symbol: str
    base_asset: str
    quote_asset: str
    min_qty: float
    min_notional: float
    qty_step: float
    price_tick: float


@dataclass(frozen=True)
class NetworkInfo:
    network: str
    deposit_enabled: bool
    withdrawal_enabled: bool
    withdrawal_fee: float
    min_withdrawal: float
    memo_required: bool = False
    confirmation_count: int = 0
    raw_chain: str = ""


class ExchangeAdapter(ABC):
    """Only SPOT capabilities belong in this interface."""

    name: str

    @abstractmethod
    def get_spot_markets(self) -> List[SpotMarket]: ...

    @abstractmethod
    def get_order_book(self, symbol: str, depth: int = 20) -> Dict[str, Any]: ...

    @abstractmethod
    def get_spot_balances(self) -> Dict[str, float]: ...

    @abstractmethod
    def get_trading_fee(self, symbol: str) -> float: ...

    @abstractmethod
    def get_networks(self, asset: str) -> List[NetworkInfo]: ...

    @abstractmethod
    def get_deposit_address(self, asset: str, network: str) -> str: ...

    def get_deposit_details(self, asset: str, network: str) -> Dict[str, str]:
        """Return destination address metadata.

        Adapters may override this to expose an exchange-required memo/tag.
        The default deliberately returns no memo so callers can fail closed
        when network metadata says a memo/tag is mandatory.
        """
        return {"address": self.get_deposit_address(asset, network), "memo": ""}

    @abstractmethod
    def place_spot_order(self, symbol: str, side: str, quantity: float, *, price: Optional[float] = None,
                         order_type: str = "LIMIT", client_order_id: Optional[str] = None) -> Dict[str, Any]: ...

    @abstractmethod
    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...

    @abstractmethod
    def withdraw_spot(self, asset: str, amount: float, address: str, network: str,
                      *, client_withdrawal_id: Optional[str] = None) -> Dict[str, Any]: ...
