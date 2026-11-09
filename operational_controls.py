from __future__ import annotations

import os
import time
from typing import Any


TRUE = {"1", "true", "yes", "on"}


def live_gate_state(cfg: dict[str, Any] | None = None) -> dict[str, bool]:
    """Return the two independent live-execution gates without enabling anything."""
    cfg = cfg or {}
    env_enabled = str(os.getenv("EXECUTION_ENABLED", "false")).lower() in TRUE
    config_enabled = bool(cfg.get("execution_live_enabled", False))
    kill_switch = (
        str(os.getenv("EXECUTION_KILL_SWITCH", "false")).lower() in TRUE
        or bool(cfg.get("execution_kill_switch", False))
    )
    return {
        "env_enabled": env_enabled,
        "config_enabled": config_enabled,
        "kill_switch": kill_switch,
        "live_enabled": env_enabled and config_enabled and not kill_switch,
    }


def preflight_live_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fail closed unless the complete controlled-live policy is explicitly enabled."""
    cfg = cfg or {}
    gates = live_gate_state(cfg)
    required = {
        "execution_live_enabled": bool(cfg.get("execution_live_enabled", False)),
        "execution_allow_market_orders": bool(cfg.get("execution_allow_market_orders", False)),
        "execution_allow_withdrawals": bool(cfg.get("execution_allow_withdrawals", False)),
        "execution_max_notional_usdt": float(cfg.get("execution_max_notional_usdt", 0) or 0),
        "execution_max_active_intents": int(cfg.get("execution_max_active_intents", 0) or 0),
        "execution_max_daily_notional_usdt": float(cfg.get("execution_max_daily_notional_usdt", 0) or 0),
    }
    warnings: list[str] = []
    if gates["live_enabled"] and required["execution_max_notional_usdt"] <= 0:
        warnings.append("live execution requires a positive notional cap")
    if gates["live_enabled"] and required["execution_max_active_intents"] <= 0:
        warnings.append("live execution requires an active-intent cap")
    if gates["live_enabled"] and required["execution_max_daily_notional_usdt"] <= 0:
        warnings.append("live execution requires a daily notional cap")
    if gates["live_enabled"] and not cfg.get("execution_allowed_exchanges"):
        warnings.append("live execution requires an exchange allowlist")
    return {
        "status": "READY" if gates["live_enabled"] and not warnings else "BLOCKED",
        "gates": gates,
        "policy": required,
        "warnings": warnings,
        "checked_at_ms": int(time.time() * 1000),
    }


def assert_controlled_live(cfg: dict[str, Any] | None = None) -> None:
    """Raise before adapter access when controlled-live policy is not safe."""
    report = preflight_live_config(cfg)
    if report["status"] != "READY":
        raise PermissionError("controlled-live operational preflight blocked execution")
