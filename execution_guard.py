#!/usr/bin/env python3
"""Hard SPOT-only execution safety gate.

Phase 5 does not execute orders yet.  This gate is the single place later
execution code must pass through.  It rejects derivatives, leverage, borrowing,
shorting and unconfirmed actions by construction.
"""

from dataclasses import dataclass
from typing import Any, Optional


ALLOWED_PRODUCT = "SPOT"
FORBIDDEN_TERMS = {
    "margin", "futures", "perpetual", "perp", "swap", "option",
    "leverage", "borrow", "short", "derivative", "isolated", "cross",
}


@dataclass(frozen=True)
class ExecutionRequest:
    product: str
    side: str
    symbol: str
    exchange: str
    quantity: float
    confirmed: bool = False
    withdrawal_confirmed: bool = False
    order_type: str = "LIMIT"
    price: Optional[float] = None


def validate_spot_request(req: ExecutionRequest, *, is_withdrawal=False):
    product = (req.product or "").upper()
    side = (req.side or "").upper()
    symbol = (req.symbol or "").lower()

    if product != ALLOWED_PRODUCT:
        raise ValueError("SPOT-ONLY policy: non-SPOT product rejected")
    if any(term in symbol for term in FORBIDDEN_TERMS):
        raise ValueError("SPOT-ONLY policy: derivative/margin symbol rejected")
    if side not in {"BUY", "SELL"}:
        raise ValueError("Only spot BUY/SELL are allowed")
    if req.quantity <= 0:
        raise ValueError("quantity must be positive")
    if not req.confirmed:
        raise PermissionError("Explicit confirmation is required for every execution")
    if is_withdrawal and not req.withdrawal_confirmed:
        raise PermissionError("Explicit withdrawal confirmation is required")
    return True


def dry_run(req: ExecutionRequest):
    """Validate an execution request without touching an exchange."""
    validate_spot_request(req, is_withdrawal=False)
    return {
        "status": "DRY_RUN",
        "product": "SPOT",
        "exchange": req.exchange,
        "side": req.side.upper(),
        "symbol": req.symbol.upper(),
        "quantity": req.quantity,
    }

def _num(cfg: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def validate_execution_order(req: ExecutionRequest, *, cfg: Optional[dict[str, Any]] = None,
                            reference_price: Optional[float] = None, signal_price: Optional[float] = None,
                            market_quality: Optional[dict[str, Any]] = None, fresh: bool = True) -> dict[str, Any]:
    """Fail-closed preflight for an order immediately before the adapter boundary."""
    cfg = cfg or {}
    validate_spot_request(req, is_withdrawal=False)
    if not bool(cfg.get("execution_live_enabled", True)):
        raise PermissionError("live execution is disabled by policy")
    allowed = cfg.get("execution_allowed_exchanges")
    if allowed and req.exchange.upper() not in {str(x).upper() for x in allowed}:
        raise PermissionError("exchange is not allowlisted for execution")
    order_type = str(req.order_type or "LIMIT").upper()
    if order_type not in {"LIMIT", "MARKET"}:
        raise ValueError("unsupported order type")
    if order_type == "MARKET" and not bool(cfg.get("execution_allow_market_orders", False)):
        raise PermissionError("market orders are disabled by execution policy")
    if not fresh:
        raise PermissionError("execution quote/revalidation data is stale")
    max_notional = _num(cfg, "execution_max_notional_usdt", 300.0)
    px = req.price if req.price is not None else reference_price
    try:
        notional = float(req.quantity) * float(px or 0.0)
    except (TypeError, ValueError):
        notional = 0.0
    if notional <= 0:
        raise ValueError("execution notional cannot be validated without a positive price")
    if notional > max_notional:
        raise PermissionError("order notional exceeds execution limit")
    if signal_price and signal_price > 0 and reference_price and reference_price > 0:
        move = abs(float(reference_price) / float(signal_price) - 1.0) * 100.0
        if move > _num(cfg, "execution_max_signal_drift_pct", 0.50):
            raise PermissionError("execution price drifted beyond signal tolerance")
    if market_quality is not None:
        if str(market_quality.get("risk_action", "ALLOW")).upper() == "BLOCK":
            raise PermissionError("market-quality gate blocked execution")
        slippage = market_quality.get("estimated_buy_slippage_pct")
        if slippage is not None and float(slippage) > _num(cfg, "execution_max_slippage_pct", 0.25):
            raise PermissionError("estimated execution slippage exceeds limit")
    return {"status":"APPROVED", "exchange":req.exchange.upper(), "symbol":req.symbol.upper(),
            "side":req.side.upper(), "order_type":order_type, "notional_usdt":round(notional,8)}


def validate_withdrawal_request(req: ExecutionRequest, *, cfg: Optional[dict[str, Any]] = None,
                                network_enabled: bool = False, destination_confirmed: bool = False,
                                network: str = "") -> dict[str, Any]:
    """Fail-closed withdrawal preflight; destination/network must be explicit."""
    cfg = cfg or {}
    validate_spot_request(req, is_withdrawal=True)
    if not bool(cfg.get("execution_allow_withdrawals", False)):
        raise PermissionError("crypto withdrawals are disabled by execution policy")
    if not network_enabled:
        raise PermissionError("validated withdrawal network is required")
    if not destination_confirmed:
        raise PermissionError("destination address/network confirmation is required")
    allowed = cfg.get("execution_allowed_withdrawal_networks") or {}
    if allowed:
        exchange = req.exchange.upper()
        networks = allowed.get(exchange) or allowed.get(exchange.lower()) or []
        if not networks or str(network).upper() not in {str(x).upper() for x in networks}:
            raise PermissionError("withdrawal network is not allowlisted")
    return {"status":"APPROVED", "exchange":req.exchange.upper(),
            "asset":req.symbol.upper().replace("USDT", ""), "network":str(network),
            "quantity":float(req.quantity)}