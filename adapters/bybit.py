#!/usr/bin/env python3
"""Authenticated Bybit SPOT adapter."""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from execution_guard import ExecutionRequest, validate_spot_request
from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket


class BybitSpotAdapter(ExchangeAdapter):
    name = "bybit"

    def _result(self, response: Dict[str, Any]) -> Dict[str, Any]:
        if int(response.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit API error: {response.get('retMsg', response)}")
        return response.get("result") or {}

    def get_spot_markets(self) -> list[SpotMarket]:
        result = self._public("GET", "/v5/market/instruments-info", params={"category": "spot"})
        rows = result.get("result", {}).get("list", []) if isinstance(result, dict) else []
        out = []
        for row in rows:
            if str(row.get("status", "")).upper() != "TRADING":
                continue
            price = row.get("priceFilter", {})
            lot = row.get("lotSizeFilter", {})
            out.append(SpotMarket(
                row["symbol"], row.get("baseCoin", ""), row.get("quoteCoin", ""),
                float(lot.get("minOrderQty", 0) or 0),
                float(lot.get("minOrderAmt", 0) or 0),
                float(lot.get("qtyStep", 0) or 0),
                float(price.get("tickSize", 0) or 0),
            ))
        return out

    def get_order_book(self, symbol: str, depth: int = 50) -> Dict[str, Any]:
        return self._public("GET", "/v5/market/orderbook", params={
            "category": "spot", "symbol": symbol.upper(), "limit": depth,
        })

    def get_spot_balances(self) -> Dict[str, float]:
        result = self._result(self._private("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
        rows = result.get("list", [])
        if not rows:
            return {}
        return {str(c.get("coin", "")).upper(): float(c.get("walletBalance", 0) or 0) for c in rows[0].get("coin", [])}

    def get_trading_fee(self, symbol: str) -> float:
        result = self._result(self._private("GET", "/v5/account/fee-rate", params={"category": "spot", "symbol": symbol.upper()}))
        rows = result.get("list", [])
        return abs(float(rows[0].get("takerFeeRate", 0) or 0)) * 100 if rows else 0.0

    def get_networks(self, asset: str) -> list[NetworkInfo]:
        result = self._result(self._private("GET", "/v5/asset/coin/query-info", params={"coin": asset.upper()}))
        rows = result.get("rows", [])
        if not rows:
            return []
        out = []
        for c in rows[0].get("chains", []):
            network = str(c.get("chain", ""))
            tag_required = str(c.get("tagRequired", "0")) == "1"
            out.append(NetworkInfo(
                network,
                c.get("chainDeposit") == "1",
                c.get("chainWithdraw") == "1',
                float(c.get("withdrawFee", 0) or 0),
                float(c.get("withdrawMin", 0) or 0),
                tag_required,
                raw_chain=network,
            ))
        return out

    def get_deposit_details(self, asset: str, network: str) -> Dict[str, str]:
        result = self._result(self._private("GET", "/v5/asset/deposit/query-address", params={"coin": asset.upper(), "chainType": network}))
        rows = result.get("rows", [])
        if not rows:
            raise RuntimeError("Bybit did not return a deposit address")
        row = rows[0]
        address = str(row.get("addressDeposit") or row.get("address") or "")
        if not address:
            raise RuntimeError("Bybit did not return a deposit address")
        tag = str(row.get("tagDeposit") or "")
        return {"address": address, "memo": tag, "memo_type": "tag" if tag else ""}

    def get_deposit_address(self, asset: str, network: str) -> str:
        return self.get_deposit_details(asset, network)["address"]

    def place_spot_order(self, symbol: str, side: str, quantity: float, *, price: Optional[float] = None, order_type: str = "LIMIT", client_order_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live execution is disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", side, symbol, self.name, quantity, True, False))
        body: Dict[str, Any] = {"category": "spot", "symbol": symbol.upper(), "side": side.title(), "orderType": order_type.title(), "qty": str(quantity), "isLeverage": 0, "timeInForce": "IOC"}
        if client_order_id:
            body["orderLinkId"] = client_order_id
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            body["price"] = str(price)
        return self._result(self._private("POST", "/v5/order/create", body=body))

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        # Bybit documents the realtime endpoint for current/unfilled state and
        # order history for older/closed records. Query realtime first, then
        # fall back to history because API propagation can be asynchronous.
        result = self._result(self._private("GET", "/v5/order/realtime", params={
            "category": "spot", "symbol": symbol.upper(), "orderId": order_id,
        }))
        rows = result.get("list", [])
        if rows:
            return rows[0]
        result = self._result(self._private("GET", "/v5/order/history", params={
            "category": "spot", "symbol": symbol.upper(), "orderId": order_id,
        }))
        rows = result.get("list", [])
        return rows[0] if rows else {}

    def withdraw_spot(self, asset: str, amount: float, address: str, network: str, *,
                      memo: Optional[str] = None, memo_type: Optional[str] = None,
                      client_withdrawal_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live withdrawals are disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", "SELL", f"{asset.upper()}USDT", self.name, amount, True, True))
        if memo and memo_type not in (None, "tag"):
            raise ValueError(f"Bybit withdrawal does not accept memo_type={memo_type!r}")
        body: Dict[str, Any] = {"coin": asset.upper(), "chain": network, "address": address, "amount": str(amount), "accountType": "UNIFIED", "forceChain": 1}
        if memo:
            body["tag"] = memo
        if client_withdrawal_id:
            body["requestId"] = client_withdrawal_id
        return self._result(self._private("POST", "/v5/asset/withdraw/create", body=body))
