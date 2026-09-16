from __future__ import annotations

import hashlib, hmac, json, os, time
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket
from execution_guard import ExecutionRequest, validate_spot_request


class MexcSpotAdapter(ExchangeAdapter):
    """MEXC Spot v3 adapter; all trade calls are explicitly SPOT endpoints."""
    name = "mexc"
    base_url = os.getenv("MEXC_BASE_URL", "https://api.mexc.com")

    def __init__(self, api_key=None, secret=None):
        self.api_key = api_key or os.getenv("MEXC_API_KEY")
        self.secret = secret or os.getenv("MEXC_API_SECRET")

    def _request(self, method, path, params=None, auth=False):
        params = dict(params or {})
        if auth:
            if not self.api_key or not self.secret: raise RuntimeError("MEXC credentials are not configured")
            params["timestamp"] = int(time.time() * 1000)
            query = urlencode(sorted(params.items()))
            params["signature"] = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        query = urlencode(params)
        url = self.base_url + path + ("?" + query if query else "")
        headers = {"X-MEXC-APIKEY": self.api_key} if auth else {}
        req = Request(url, headers=headers, method=method.upper())
        with urlopen(req, timeout=10) as response: return json.loads(response.read().decode())

    def _trade(self, method, path, params):
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true": raise RuntimeError("Live execution is disabled")
        return self._request(method, path, params, auth=True)

    def get_spot_markets(self):
        data = self._request("GET", "/api/v3/exchangeInfo")
        out = []
        for x in data.get("symbols", []):
            if str(x.get("status", "")).upper() not in ("1", "ENABLED", "ONLINE", ""):
                continue
            filters = {f.get("filterType"): f for f in x.get("filters", [])}
            lot = filters.get("LOT_SIZE", {})
            notional = filters.get("MIN_NOTIONAL", {})
            out.append(SpotMarket(x["symbol"], x["baseAsset"], x["quoteAsset"], float(lot.get("minQty", 0) or 0), float(notional.get("minNotional", 0) or 0), float(lot.get("stepSize", 0) or 0), float(filters.get("PRICE_FILTER", {}).get("tickSize", 0) or 0)))
        return out

    def get_order_book(self, symbol, depth=20):
        return self._request("GET", "/api/v3/depth", {"symbol": symbol.upper(), "limit": min(depth, 5000)})

    def get_spot_balances(self):
        data = self._request("GET", "/api/v3/account", auth=True)
        return {str(x.get("asset", "")).upper(): float(x.get("free", 0) or 0) for x in data.get("balances", [])}

    def get_trading_fee(self, symbol):
        data = self._request("GET", "/api/v3/account/tradeFee", {"symbol": symbol.upper()}, auth=True)
        if isinstance(data, list): data = data[0] if data else {}
        return float(data.get("takerCommission", data.get("takerFeeRate", 0)) or 0) * 100

    def get_networks(self, asset):
        data = self._request("GET", "/api/v3/capital/config/getall", auth=True)
        coin = next((x for x in data if str(x.get("coin", "")).upper() == asset.upper()), None)
        if not coin: return []
        return [NetworkInfo(str(x.get("network", "")), bool(x.get("depositEnable", False)), bool(x.get("withdrawEnable", False)), float(x.get("withdrawFee", 0) or 0), float(x.get("withdrawMin", 0) or 0), bool(x.get("memoRequired", False) or x.get("needTag", False)), raw_chain=str(x.get("network", ""))) for x in coin.get("networkList", [])]

    def get_deposit_details(self, asset, network):
        data = self._request("GET", "/api/v3/capital/deposit/address", {"coin": asset.upper(), "network": network}, auth=True)
        address = str(data.get("address", ""))
        if not address: raise RuntimeError("MEXC did not return a deposit address")
        memo = str(data.get("memo") or "")
        return {"address": address, "memo": memo, "memo_type": "memo" if memo else ""}

    def get_deposit_address(self, asset, network):
        return self.get_deposit_details(asset, network)["address"]

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true": raise RuntimeError("Live execution is disabled")
        side = side.upper(); order_type = order_type.upper()
        if side not in ("BUY", "SELL") or order_type not in ("LIMIT", "MARKET"): raise ValueError("MEXC adapter accepts SPOT BUY/SELL only")
        validate_spot_request(ExecutionRequest("SPOT", side, symbol, self.name, quantity, True, False))
        params = {"symbol": symbol.upper(), "side": side, "type": order_type, "quantity": str(quantity)}
        if order_type == "LIMIT":
            if price is None: raise ValueError("LIMIT order requires price")
            params.update({"price": str(price), "timeInForce": "GTC"})
        if client_order_id: params["newClientOrderId"] = client_order_id
        return self._trade("POST", "/api/v3/order", params)

    def get_order(self, symbol, order_id):
        return self._request("GET", "/api/v3/order", {"symbol": symbol.upper(), "orderId": order_id}, auth=True)

    def withdraw_spot(self, asset, amount, address, network, *, memo: Optional[str] = None, memo_type: Optional[str] = None, client_withdrawal_id=None):
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true": raise RuntimeError("Live withdrawals are disabled")
        validate_spot_request(ExecutionRequest("SPOT", "SELL", f"{asset.upper()}USDT", self.name, amount, True, True))
        if memo and memo_type not in (None, "memo"):
            raise ValueError(f"MEXC withdrawal does not accept memo_type={memo_type!r}")
        params = {"coin": asset.upper(), "amount": str(amount), "address": address, "network": network}
        if memo: params["memo"] = memo
        if client_withdrawal_id: params["withdrawOrderId"] = client_withdrawal_id
        return self._trade("POST", "/api/v3/capital/withdraw/apply", params)
