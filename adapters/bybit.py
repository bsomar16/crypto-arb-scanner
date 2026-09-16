#!/usr/bin/env python3
"""Bybit V5 authenticated SPOT adapter.

Every trade request pins category=spot and isLeverage=0. No derivatives or
margin parameters are exposed through this adapter.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket
from execution_guard import ExecutionRequest, validate_spot_request
from .http import request_json


class BybitSpotAdapter(ExchangeAdapter):
    name = "bybit"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 base_url: str = "https://api.bybit.com") -> None:
        self.api_key = api_key or os.getenv("BYBIT_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BYBIT_API_SECRET", "")
        self.base_url = base_url.rstrip("/")
        self.recv_window = os.getenv("BYBIT_RECV_WINDOW", "5000")

    def _private(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Bybit API credentials are not configured")
        method = method.upper()
        timestamp = str(int(time.time() * 1000))
        if method == "GET":
            query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
            payload = timestamp + self.api_key + self.recv_window + query
        else:
            body_text = json.dumps(body or {}, separators=(",", ":"))
            payload = timestamp + self.api_key + self.recv_window + body_text
        signature = hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": timestamp,
                   "X-BAPI-RECV-WINDOW": self.recv_window, "X-BAPI-SIGN": signature,
                   "Content-Type": "application/json"}
        return request_json(method, self.base_url + path, params=params, body=body, headers=headers)

    @staticmethod
    def _result(data: Any) -> Any:
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error {data.get('retCode')}: {data.get('retMsg')}")
        return data.get("result", {})

    def get_spot_markets(self) -> List[SpotMarket]:
        result = self._result(request_json("GET", self.base_url + "/v5/market/instruments-info", params={"category": "spot"}))
        out = []
        for r in result.get("list", []):
            if r.get("status") != "Trading":
                continue
            pf = r.get("lotSizeFilter", {})
            price = r.get("priceFilter", {})
            out.append(SpotMarket(
                r["symbol"], r["baseCoin"], r["quoteCoin"],
                float(pf.get("minOrderQty", 0) or 0),
                float(pf.get("minOrderAmt", 0) or 0),
                float(pf.get("qtyStep", 0) or 0),
                float(price.get("tickSize", 0) or 0),
            ))
        return out

    def get_order_book(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        return self._result(request_json("GET", self.base_url + "/v5/market/orderbook", params={"category": "spot", "symbol": symbol.upper(), "limit": min(depth, 200)}))

    def get_spot_balances(self) -> Dict[str, float]:
        result = self._result(self._private("GET", "/v5/account/wallet-balance", params={"accountType": "UNIFIED"}))
        out: Dict[str, float] = {}
        for coin in (result.get("list") or [{}])[0].get("coin", []):
            value = float(coin.get("walletBalance", 0) or 0) - float(coin.get("spotBorrow", 0) or 0)
            if value > 0:
                out[coin["coin"]] = value
        return out

    def get_trading_fee(self, symbol: str) -> float:
        result = self._result(self._private("GET", "/v5/account/fee-rate", params={"category": "spot", "symbol": symbol.upper()}))
        rows = result.get("list", [])
        if not rows:
            raise RuntimeError("Bybit did not return a spot fee")
        return float(rows[0].get("takerFeeRate", 0)) * 100.0

    def get_networks(self, asset: str) -> List[NetworkInfo]:
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
                c.get("chainWithdraw") == "1",
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
        result = self._result(self._private("GET", "/v5/order/history", params={"category": "spot", "symbol": symbol.upper(), "orderId": order_id}))
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
