#!/usr/bin/env python3
"""Hard SPOT-only execution safety gate.

Phase 5 does not execute orders yet.  This gate is the single place later
execution code must pass through.  It rejects derivatives, leverage, borrowing,
shorting and unconfirmed actions by construction.
"""

from dataclasses import dataclass
from typing import Optional


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
