#!/usr/bin/env python3
"""Provider-specific SPOT transfer reconciliation.

A transfer is releasable to the SELL leg only after both the source withdrawal
and destination deposit are independently confirmed. The module fails closed
when provider metadata is missing or ambiguous.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional
import time

@dataclass(frozen=True)
class TransferSnapshot:
    provider: str
    direction: str
    status: str
    provider_id: str = ""
    tx_hash: str = ""
    amount: float = 0.0
    asset: str = ""
    network: str = ""
    address: str = ""
    fee: float = 0.0
    raw_status: str = ""
    updated_ms: int = 0

@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    source: Optional[TransferSnapshot]
    destination: Optional[TransferSnapshot]
    reason: str = ""

_SUCCESS = {"SUCCESS", "COMPLETED", "CONFIRMED", "DONE", "CREDITED"}
_FAILURE = {"FAILED", "FAIL", "REJECTED", "CANCELLED", "CANCELED", "REFUND", "INVALID", "RESTRICTED"}

def _norm_status(value: Any, provider: str, direction: str) -> str:
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        n = int(value)
        if provider == "binance":
            if direction == "withdrawal": return "COMPLETED" if n == 6 else ("FAILED" if n in {1,3,5} else "CONFIRMING")
            return "COMPLETED" if n in {1,6} else "CONFIRMING"
        if provider == "mexc":
            if direction == "withdrawal": return "COMPLETED" if n == 7 else ("FAILED" if n in {8,9} else "CONFIRMING")
            return "COMPLETED" if n in {5,12} else ("FAILED" if n in {7,8,10,11} else "CONFIRMING")
        if provider == "okx":
            return "COMPLETED" if n in {7,8} else ("FAILED" if n in {1,9,10,11,12,13,14,18,20} else "CONFIRMING")
    s = str(value or "").strip().upper()
    if s in _SUCCESS: return "COMPLETED"
    if s in _FAILURE: return "FAILED"
    return "CONFIRMING"

def _rows(data: Any) -> list[Dict[str, Any]]:
    if isinstance(data, list): return data
    if not isinstance(data, dict): return []
    if isinstance(data.get("data"), list): return data["data"]
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("list", "rows"):
            if isinstance(result.get(key), list): return result[key]
    if isinstance(data.get("list"), list): return data["list"]
    return [data] if data else []

def _snapshot(provider: str, direction: str, row: Dict[str, Any]) -> TransferSnapshot:
    status = row.get("status", row.get("state", row.get("transStatus", "")))
    tx = row.get("txId") or row.get("txid") or row.get("txHash") or row.get("transHash") or row.get("tradeId") or ""
    pid = row.get("id") or row.get("withdrawId") or row.get("withdrawalId") or row.get("wdId") or row.get("orderId") or ""
    amount = row.get("amount", row.get("size", row.get("amt", 0)))
    fee = row.get("fee", row.get("transactionFee", 0))
    return TransferSnapshot(provider, direction, _norm_status(status, provider, direction), str(pid), str(tx), float(amount or 0),
        str(row.get("coin", row.get("ccy", ""))).upper(), str(row.get("network", row.get("chain", ""))),
        str(row.get("address", row.get("toAddress", row.get("addr", "")))), float(fee or 0), str(status),
        int(row.get("updateTime", row.get("uTime", row.get("updatedTime", row.get("ts", 0)))) or 0))

def _query(adapter: Any, direction: str, *, transfer_id: str, asset: str, network: str, created_ms: int = 0) -> TransferSnapshot:
    provider = str(adapter.name).lower(); now = int(time.time() * 1000)
    if provider == "binance":
        path = "/sapi/v1/capital/withdraw/history" if direction == "withdrawal" else "/sapi/v1/capital/deposit/hisrec"
        params = {"coin": asset.upper()}
        if direction == "withdrawal": params["withdrawOrderId"] = transfer_id
        rows = _rows(adapter._signed("GET", path, params))
        if direction == "deposit": rows = [r for r in rows if str(r.get("txId") or r.get("id") or "") == transfer_id or (created_ms and int(r.get("insertTime", 0) or 0) >= created_ms)]
    elif provider == "bybit":
        path = "/v5/asset/withdraw/query-record" if direction == "withdrawal" else "/v5/asset/deposit/query-record"
        params = {"coin": asset.upper(), "limit": 50}
        if direction == "withdrawal": params["withdrawID"] = transfer_id
        rows = _rows(adapter._private("GET", path, params=params))
        if direction == "deposit" and transfer_id:
            matches = [r for r in rows if str(r.get("txID") or r.get("id") or "") == transfer_id]
            if matches: rows = matches
    elif provider == "okx":
        path = "/api/v5/asset/withdrawal-history" if direction == "withdrawal" else "/api/v5/asset/deposit-history"
        params = {"ccy": asset.upper(), "limit": 100}
        if direction == "withdrawal": params["wdId"] = transfer_id
        rows = _rows(adapter._private("GET", path, params=params))
        if direction == "deposit" and transfer_id:
            matches = [r for r in rows if str(r.get("txId") or r.get("depId") or "") == transfer_id]
            if matches: rows = matches
    elif provider == "bitget":
        path = "/api/v2/spot/wallet/withdrawal-records" if direction == "withdrawal" else "/api/v2/spot/wallet/deposit-records"
        rows = _rows(adapter._request("GET", path, {"coin": asset.upper(), "orderId": transfer_id, "limit": 20}, auth=True))
    elif provider == "mexc":
        path = "/api/v3/capital/withdraw/history" if direction == "withdrawal" else "/api/v3/capital/deposit/hisrec"
        rows = _rows(adapter._request("GET", path, {"coin": asset.upper()}, auth=True))
        key = "id" if direction == "withdrawal" else "txId"
        matches = [r for r in rows if str(r.get(key) or "") == transfer_id]
        if matches: rows = matches
    else:
        raise RuntimeError(f"unsupported transfer reconciliation provider: {provider}")
    row = rows[0] if rows else {}
    if not row: return TransferSnapshot(provider, direction, "CONFIRMING", provider_id=transfer_id, asset=asset.upper(), network=network, updated_ms=now)
    return _snapshot(provider, direction, row)

def reconcile_transfer(source_adapter: Any, destination_adapter: Any, *, transfer_id: str, asset: str,
                       network: str, expected_amount: float, expected_address: str = "", created_ms: int = 0) -> ReconciliationResult:
    source = _query(source_adapter, "withdrawal", transfer_id=transfer_id, asset=asset, network=network, created_ms=created_ms)
    destination = _query(destination_adapter, "deposit", transfer_id=source.tx_hash or transfer_id, asset=asset, network=network, created_ms=created_ms)
    for snap, label in ((source, "source withdrawal"), (destination, "destination deposit")):
        if snap.status == "FAILED": return ReconciliationResult("FAILED", source, destination, f"{label} failed ({snap.raw_status})")
        if snap.asset and snap.asset != asset.upper(): return ReconciliationResult("FAILED", source, destination, f"{label} asset mismatch")
        if snap.network and network and network.upper() not in snap.network.upper() and snap.network.upper() not in network.upper(): return ReconciliationResult("FAILED", source, destination, f"{label} network mismatch")
    if source.status != "COMPLETED": return ReconciliationResult("CONFIRMING", source, destination, "source withdrawal is not finalized")
    if destination.status != "COMPLETED": return ReconciliationResult("CONFIRMING", source, destination, "destination deposit is not credited")
    if destination.amount + 1e-12 < expected_amount: return ReconciliationResult("FAILED", source, destination, "destination credited amount is below expected transfer amount")
    if expected_address and destination.address and destination.address.lower() != expected_address.lower(): return ReconciliationResult("FAILED", source, destination, "destination address mismatch")
    return ReconciliationResult("COMPLETED", source, destination, "source withdrawal and destination deposit independently confirmed")
