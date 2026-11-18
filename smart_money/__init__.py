"""Smart Money analytical subsystem for Binance Spot crypto trading.

Provides Volume Intelligence, CVD (Cumulative Volume Delta), Absorption Detection,
Order Book Depth Imbalance (DIR), and Wyckoff Accumulation Structure.

Strictly Binance Spot Long/BUY only. No futures, no margin, no leverage, no shorting.
"""
from __future__ import annotations

from .volume_cvd import (
    compute_volume_cvd_analytics,
    cvd_linear_regression_slope,
    VolumeCvdResult,
)
from .accumulation import (
    evaluate_accumulation_structure,
    calculate_orderbook_dir,
    validate_accumulation_weights,
    DEFAULT_ACCUMULATION_WEIGHTS,
    AccumulationResult,
)

__all__ = [
    "compute_volume_cvd_analytics",
    "cvd_linear_regression_slope",
    "VolumeCvdResult",
    "evaluate_accumulation_structure",
    "calculate_orderbook_dir",
    "validate_accumulation_weights",
    "DEFAULT_ACCUMULATION_WEIGHTS",
    "AccumulationResult",
]
