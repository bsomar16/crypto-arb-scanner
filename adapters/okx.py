#!/usr/bin/env python3
"""OKX authenticated SPOT adapter using the v5 API."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket
from execution_guard import ExecutionRequest, validate_spot_request
from .http import request_json


class OKXSpotAdapter(ExchangeAdapter):
    name = "okx"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 passphrase: Optional[str] = None, base_url: str = "https://openapi.okx.com") -> None:
        self.api_key = api_key or os.getenv("OKX_API_KEY", "")
        self.api_secret = api_secret or os.getenv("OKX_API_SECRET", "")
        self.passphrase = passphrase or os.getenv("OKX_PASSPHRASE", "")
        self.base_url = base_url.rstrip("/")

    def _private(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                 body: Optional[Dict[str, Any]] = None) -> Any:
        if not self.api_key or not self.api_secret or not self.passphrase:
            raise RuntimeError("OKX API credentials are not configured")
        method = method.upper()
        query = ""
        if params:
            from urllib.parse import urlencode
            query = "?" + urlencode(params)
        body_text = json.dumps(body, separators=(",", ":")) if body else ""
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prehash = timestamp + method + path + query + body_text
        digest = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode()
        headers = {"OK-ACCESS-KEY": self.api_key, "OK-ACCESS-SIGN": signature,
                   "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": self.passphrase,
                   "Content-Type": "application/json"}
        return request_json(method, self.base_url + path, params=params, body=body, headers=headers)

    @staticmethod
    def _rows(data: Any) -> List[Dict[str, Any]]:
        if not isinstance(data, dict) or data.get("code") not in (None, "0"):
            raise RuntimeError(f"OKX API error: {data}")
        return list(data.get("data", []))

    def get_spot_markets(self) -> List[SpotMarket]:
        rows = self._rows(request_json("GET", self.base_url + "/api/v5/public/instruments",
                                       params={"instType": "SPOT"}))
        return [SpotMarket(r["instId"], r["baseCcy"], r["quoteCcy"], float(r.get("minSz", 0) or 0),
                           0.0, float(r.get("lotSz", 0) or 0), float(r.get("tickSz", 0) or 0))
                for r in rows if r.get("state") == "live"]

    def get_order_book(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        rows = self._rows(request_json("GET", self.base_url + "/api/v5/market/books",
                                       params={"instId": symbol.upper(), "sz": min(depth, 400)}))
        return rows[0] if rows else {"asks": [], "bids": []}

    def get_spot_balances(self) -> Dict[str, float]:
        rows = self._rows(self._private("GET", "/api/v5/account/balance"))
        out: Dict[str, float] = {}
        for account in rows:
            for detail in account.get("details", []):
                value = float(detail.get("availBal", 0) or 0)
                if value > 0:
                    out[detail["ccy"]] = value
        return out

    def get_trading_fee(self, symbol: str) -> float:
        rows = self._rows(self._private("GET", "/api/v5/account/trade-fee",
                                        params={"instType": "SPOT", "instId": symbol.upper()}))
        if not rows:
            raise RuntimeError("OKX did not return a trading fee")
        # Provider-neutral contract returns percentage points, e.g. 0.08 for 0.08%.
        return abs(float(rows[0].get("taker", 0) or 0)) * 100.0

    def get_networks(self, asset: str) -> List[NetworkInfo]:
        rows = self._rows(self._private("GET", "/api/v5/asset/currencies", params={"ccy": asset.upper()}))
        out = []
        for r in rows:
            chain = str(r.get("chain", ""))
            memo_required = bool(r.get("needTag", False) or r.get("tagRequired", False) or r.get("needMemo", False))
            out.append(NetworkInfo(chain.replace(asset.upper() + "-", ""), bool(r.get("canDep", False)),
                                   bool(r.get("canWd", False)), float(r.get("fee", 0) or 0),
                                   float(r.get("minWd", 0) or 0), memo_required, raw_chain=chain))
        return out

    def get_deposit_details(self, asset: str, network: str) -> Dict[str, str]:
        rows = self._rows(self._private("GET", "/api/v5/asset/deposit-address", params={"ccy": asset.upper()}))
        for row in rows:
            chain = str(row.get("chain", ""))
            if network.upper() not in chain.upper() and not chain.upper().endswith(network.upper()):
                continue
            address = str(row.get("addr") or "")
            if not address:
                raise RuntimeError("OKX did not return a deposit address")
            if row.get("tag") not in (None, ""):
                return {"address": address, "memo": str(row["tag"]), "memo_type": "tag"}
            if row.get("memo") not in (None, ""):
                return {"address": address, "memo": str(row["memo"]), "memo_type": "memo"}
            if row.get("pmtId") not in (None, ""):
                return {"address": address, "memo": str(row["pmtId"]), "memo_type": "payment_id"}
            if row.get("addrEx"):
                raise RuntimeError("OKX destination requires address attachment metadata not representable by the adapter")
            return {"address": address, "memo": "", "memo_type": ""}
        raise RuntimeError(f"OKX did not return a deposit address for {asset}/{network}")

    def get_deposit_address(self, asset: str, network: str) -> str:
        return self.get_deposit_details(asset, network)["address"]

    def place_spot_order(self, symbol: str, side: str, quantity: float, *, price: Optional[float] = None,
                         order_type: str = "LIMIT", client_order_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live execution is disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", side, symbol, self.name, quantity, True, False))
        body: Dict[str, Any] = {"instId": symbol.upper(), "tdMode": "cash", "side": side.lower(),
                                "ordType": order_type.lower(), "sz": str(quantity)}
        if client_order_id:
            body["clOrdId"] = client_order_id
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            body["px"] = str(price)
        return self._private("POST", "/api/v5/trade/order", body=body)

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        rows = self._rows(self._private("GET", "/api/v5/trade/order",
                                        params={"instId": symbol.upper(), "ordId": order_id}))
        return rows[0] if rows else {}

    def withdraw_spot(self, asset: str, amount: float, address: str, network: str, *,
                      memo: Optional[str] = None, memo_type: Optional[str] = None,
                      client_withdrawal_id: Optional[str] = None) -> Dict[str, Any]:
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("live withdrawals are disabled; set EXECUTION_ENABLED=true deliberately")
        validate_spot_request(ExecutionRequest("SPOT", "SELL", f"{asset.upper()}USDT", self.name,
                                                amount, True, True))
        if memo_type == "tag":
            raise ValueError("OKX withdrawal tags require explicit provider-specific routing; use memo/payment metadata")
        body: Dict[str, Any] = {"ccy": asset.upper(), "amt": str(amount), "dest": "4",
                                "toAddr": address, "chain": network}
        if memo:
            if memo_type == "payment_id":
                body["pmtId"] = memo
            else:
                body["memo"] = memo
        if client_withdrawal_id:
            body["clientId"] = client_withdrawal_id
        return self._private("POST", "/api/v5/asset/withdrawal", body=body)
