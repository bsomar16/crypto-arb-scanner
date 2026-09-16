#!/usr/bin/env python3
"""Binance authenticated SPOT adapter.

Only Spot endpoints are exposed. Withdrawal is deliberately fail-closed unless
EXECUTION_ENABLED=true; the higher-level execution confirmation is still
required by execution_engine.py.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket
from execution_guard import ExecutionRequest, validate_spot_request
from .http import request_json


class BinanceSpotAdapter(ExchangeAdapter):
    name = "binance"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 base_url: str = "https://api.binance.com") -> None:
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self.base_url = base_url.rstrip("/")
        self.recv_window = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))

    def _signed(self, method: str, path: str, params: Dict[str, Any]) -> Any:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Binance API credentials are not configured")
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.recv_window
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return request_json(method, self.base_url + path, params=params,
                            headers={"X-MBX-APIKEY": self.api_key})

    def get_spot_markets(self) -> List[SpotMarket]:
        data = request_json("GET", self.base_url + "/api/v3/exchangeInfo")
        out: List[SpotMarket] = []
        for item in data.get("symbols", []):
            if item.get("status") != "TRADING" or item.get("isSpotTradingAllowed") is False:
                continue
            filters = {f.get("filterType"): f for f in item.get("filters", [])}
            lot = filters.get("LOT_SIZE", {})
            notion = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
            price = filters.get("PRICE_FILTER", {})
            out.append(SpotMarket(item["symbol"], item["baseAsset"], item["quoteAsset"],
                                  float(lot.get("minQty", 0)), float(notion.get("minNotional", 0)),
                                  float(lot.get("stepSize", 0)), float(price.get("tickSize", 0))))
        return out

    def get_order_book(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        return request_json("GET", self.base_url + "/api/v3/depth",
                            params={"symbol": symbol.upper(), "limit": depth})

    def get_spot_balances(self) -> Dict[str, float]:
        data = self._signed("GET", "/api/v3/account", {})
        return {b["asset"]: float(b["free"]) for b in data.get("balances", [])
                if float(b.get("free", 0)) > 0}

    def get_trading_fee(self, symbol: str) -> float:
        rows = self._signed("GET", "/sapi/v1/asset/tradeFee", {"symbol": symbol.upper()})
        row = rows[0] if isinstance(rows, list) and rows else rows
        return float(row.get("takerCommission", row.get("taker", 0))) * 100.0

    def get_networks(self, asset: str) -> List[NetworkInfo]:
        rows = self._signed("GET", "/sapi/v1/capital/config/getall", {})
        for coin in rows:
            if coin.get("coin", "").upper() != asset.upper():
                continue
            out = []
            for n in coin.get("networkList", []):
                out.append(NetworkInfo(str(n.get("network", "")), bool(n.get("depositEnable")),
                                      bool(n.get("withdrawEnable")), float(n.get("withdrawFee", 0)),
                                      float(n.get("withdrawMin", 0))))
            return out
        return []

    def get_deposit_address(self, asset: str, network: str) -> str:
        data = self._signed("GET", "/sapi/v1/capital/deposit/address",
                            {"coin": asset.upper(), "network": network})
        address = data.get("address")
        if not address:
            raise RuntimeError("Binance did not return a deposit address")
        return str(address)

    def place_spot_order(self, symbol: str, side: str, quantity: float, *, price: Optional[float] = None,
                         order_type: str = "LIMIT", client_order_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live execution is disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", side, symbol, self.name, quantity, True, False))
        params: Dict[str, Any] = {"symbol": symbol.upper(), "side": side.upper(), "type": order_type.upper(),
                                  "quantity": quantity}
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            params.update({"price": price, "timeInForce": "IOC"})
        return self._signed("POST", "/api/v3/order", params)

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return self._signed("GET", "/api/v3/order", {"symbol": symbol.upper(), "orderId": order_id})

    def withdraw_spot(self, asset: str, amount: float, address: str, network: str, *,
                      client_withdrawal_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live withdrawals are disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", "SELL", f"{asset.upper()}USDT", self.name,
                                                amount, True, True))
        params: Dict[str, Any] = {"coin": asset.upper(), "amount": amount, "address": address,
                                  "network": network}
        if client_withdrawal_id:
            params["withdrawOrderId"] = client_withdrawal_id
        return self._signed("POST", "/sapi/v1/capital/withdraw/apply", params)
