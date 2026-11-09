from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

REQUIRED_MODULES = (
    "execution_guard",
    "execution_engine",
    "execution_recovery",
    "two_leg_execution",
    "two_leg_executor",
    "execution_monitor",
    "operational_controls",
    "telegram_control",
)

REQUIRED_CONFIG = (
    "execution_live_enabled",
    "execution_max_notional_usdt",
    "execution_max_active_intents",
    "execution_max_daily_notional_usdt",
    "execution_allow_market_orders",
    "execution_allow_withdrawals",
    "execution_allowed_exchanges",
    "execution_kill_switch",
)


def audit_config(cfg: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_CONFIG if key not in cfg]
    issues: list[str] = []
    if bool(cfg.get("execution_live_enabled", False)):
        if float(cfg.get("execution_max_notional_usdt", 0) or 0) <= 0:
            issues.append("execution_max_notional_usdt must be positive")
        if int(cfg.get("execution_max_active_intents", 0) or 0) <= 0:
            issues.append("execution_max_active_intents must be positive")
        if float(cfg.get("execution_max_daily_notional_usdt", 0) or 0) <= 0:
            issues.append("execution_max_daily_notional_usdt must be positive")
        if not cfg.get("execution_allowed_exchanges"):
            issues.append("execution_allowed_exchanges must not be empty")
    return {"missing": missing, "issues": issues}


def run_audit(config_path: str | Path = "config.json") -> dict[str, Any]:
    path = Path(config_path)
    issues: list[str] = []
    modules: dict[str, bool] = {}
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
            modules[name] = True
        except Exception as exc:
            modules[name] = False
            issues.append(f"module {name} failed to import: {exc}")

    cfg: dict[str, Any] = {}
    if not path.exists():
        issues.append(f"configuration file not found: {path}")
    else:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                issues.append("configuration root must be an object")
                cfg = {}
        except Exception as exc:
            issues.append(f"configuration is not valid JSON: {exc}")

    config_report = audit_config(cfg)
    issues.extend(f"config missing: {x}" for x in config_report["missing"])
    issues.extend(config_report["issues"])

    # Production must remain opt-in. A repository config that enables live
    # execution is a hard audit failure, even if the runtime env gate is off.
    if bool(cfg.get("execution_live_enabled", False)):
        issues.append("repository config must keep execution_live_enabled=false")

    # The controlled-live design intentionally keeps these dangerous capabilities
    # disabled until a separately reviewed activation step.
    if bool(cfg.get("execution_allow_market_orders", False)):
        issues.append("market orders must remain disabled in the production baseline")
    if bool(cfg.get("execution_allow_withdrawals", False)):
        issues.append("withdrawals must remain disabled in the production baseline")
    if bool(cfg.get("execution_kill_switch", False)):
        issues.append("execution kill switch should not be configured as an active baseline state")

    return {
        "status": "PASS" if not issues else "FAIL",
        "modules": modules,
        "config": config_report,
        "issues": issues,
    }


if __name__ == "__main__":
    report = run_audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
