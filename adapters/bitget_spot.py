from __future__ import annotations

import base64, hashlib, hmac, json, os, time
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from exchange_adapter import ExchangeAdapter, NetworkInfo, SpotMarket
from execution_guard import ExecutionRequest, validate_spot_request


class BitgetSpotAdapter(ExchangeAdapter):
    """Bitget UTA SPOT adapter. No margin/futures parameters are exposed."""
    name = "bitget"
    base_url = os.getenv("BITGET_BASE_URL", "https://api.bitget.com")

    def __init__(self, api_key=None, secret=None, passphrase=None):
        self.api_key = api_key or os.getenv("BITGET_API_KEY")
        self.secret = secret or os.getenv("BITGET_API_SECRET")
        self.passphrase = passphrase or os.getenv("BITGET_API_PASSPHRASE")

    def _request(self, method, path, params=None, body=None, auth=False):
        params = params or {}
        body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        query = urlencode(sorted(params.items())) if params else ""
        request_path = f"{path}?{query}" if query else path
        headers = {"Content-Type": "application/json", "locale": "en-US"}
        if auth:
            if not all((self.api_key, self.secret, self.passphrase)):
                raise RuntimeError("Bitget credentials are not configured")
            ts = str(int(time.time() * 1000))
            prehash = ts + method.upper() + request_path + body_text
            sign = base64.b64encode(hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
            headers.update({"ACCESS-KEY": self.api_key, "ACCESS-SIGN": sign, "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": self.passphrase})
        req = Request(self.base_url + request_path, data=body_text.encode() if body_text else None, headers=headers, method=method.upper())
        with urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode())
        if str(payload.get("code")) != "00000":
            raise RuntimeError(f"Bitget API error: {payload.get('msg', payload)}")
        return payload.get("data", payload)

    def get_spot_markets(self):
        data = self._request("GET", "/api/v2/spot/public/symbols")
        return [SpotMarket(str(x.get("symbol", "")).upper(), str(x.get("baseCoin", "")).upper(), str(x.get("quoteCoin", "")).upper(), float(x.get("minTradeAmount", 0) or 0), float(x.get("minTradeUSDT", 0) or 0), float(x.get("minTradeAmount", 0) or 0), float(x.get("pricePrecision", 0) or 0)) for x in (data or [])]

    def get_order_book(self, symbol, depth=20):
        return self._request("GET", "/api/v2/spot/market/orderbook", {"symbol": symbol.upper(), "type": "step0", "limit": min(depth, 150)})

    def get_spot_balances(self):
        data = self._request("GET", "/api/v3/account/assets", {"accountType": "UNIFIED"}, auth=True)
        return {str(x.get("coin", "")).upper(): float(x.get("available", x.get("availableBalance", 0)) or 0) for x in (data or [])}

    def get_trading_fee(self, symbol):
        data = self._request("GET", "/api/v3/account/fee-rate", {"category": "SPOT", "symbol": symbol.upper()}, auth=True)
        if isinstance(data, list): data = data[0] if data else {}
        return float(data.get("takerFeeRate", data.get("takerFee", 0)) or 0) * 100

    def get_networks(self, asset):
        data = self._request("GET", "/api/v2/spot/public/coins", {"coin": asset.upper()})
        return [NetworkInfo(str(x.get("chain", "")), bool(x.get("rechargeable", True)), bool(x.get("withdrawable", True)), float(x.get("withdrawFee", 0) or 0), float(x.get("minWithdrawAmount", 0) or 0), bool(x.get("tag", False) or x.get("needTag", False)), raw_chain=str(x.get("chain", ""))) for x in (data or [])]

    def get_deposit_address(self, asset, network):
        data = self._request("GET", "/api/v2/spot/wallet/deposit-address", {"coin": asset.upper(), "chain": network}, auth=True)
        return str(data.get("address", ""))

    def place_spot_order(self, symbol, side, quantity, *, price=None, order_type="LIMIT", client_order_id=None):
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError("Live execution is disabled")
        side = side.upper(); order_type = order_type.upper()
        if side not in ("BUY", "SELL") or order_type not in ("LIMIT", "MARKET"):
            raise ValueError("Bitget adapter accepts SPOT BUY/SELL only")
        validate_spot_request(ExecutionRequest("SPOT", side, symbol, self.name, quantity, True, False))
        body = {"category": "SPOT", "symbol": symbol.upper(), "qty": str(quantity), "side": side.lower(), "orderType": order_type.lower()}
        if order_type == "LIMIT":
            if price is None: raise ValueError("LIMIT order requires price")
            body.update({"price": str(price), "timeInForce": "gtc"})
        else: body["timeInForce"] = "ioc"
        if client_order_id: body["clientOid"] = client_order_id
        return self._request("POST", "/api/v3/trade/place-order", body=body, auth=True)

    def get_order(self, symbol, order_id):
        return self._request("GET", "/api/v3/trade/order-info", {"category": "SPOT", "symbol": symbol.upper(), "orderId": order_id}, auth=True)

    def withdraw_spot(self, asset, amount, address, network, *, client_withdrawal_id=None):
        if os.getenv("EXECUTION_ENABLED", "false").lower() != "true": raise RuntimeError("Live withdrawals are disabled")
        validate_spot_request(ExecutionRequest("SPOT", "SELL", f"{asset.upper()}USDT", self.name, amount, True, True))
        body = {"coin": asset.upper(), "transferType": "on_chain", "address": address, "chain": network, "size": str(amount)}
        if client_withdrawal_id: body["clientOid"] = client_withdrawal_id
        return self._request("POST", "/api/v2/spot/wallet/withdrawal", body=body, auth=True)
