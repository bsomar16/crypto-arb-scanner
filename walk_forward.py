#!/usr/bin/env python3
"""Chronological walk-forward backtesting for the crypto SPOT signal engine.

The evaluator keeps training evidence strictly before each validation/test
window. It never writes to live signal history and labels all results as
walk_forward/backtest evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable, Iterable, Sequence
import json
import os


DEFAULT_TRAIN_BARS = 2000
DEFAULT_TEST_BARS = 500
DEFAULT_STEP_BARS = 250
DEFAULT_MIN_TRAIN_BARS = 500
DEFAULT_MAX_WINDOWS = 20
PATH = "state/walk_forward_stats.json"


def _ts(row: Sequence[Any]) -> int:
    return int(row[0])


def validate_rows(rows: Iterable[Sequence[Any]]) -> list[Sequence[Any]]:
    """Return chronologically sorted rows and reject duplicate timestamps."""
    data = list(rows or [])
    data.sort(key=_ts)
    stamps = [_ts(row) for row in data]
    if len(stamps) != len(set(stamps)):
        raise ValueError("walk-forward input contains duplicate candle timestamps")
    return data


def build_windows(
    rows: Sequence[Sequence[Any]],
    train_bars: int = DEFAULT_TRAIN_BARS,
    test_bars: int = DEFAULT_TEST_BARS,
    step_bars: int = DEFAULT_STEP_BARS,
    min_train_bars: int = DEFAULT_MIN_TRAIN_BARS,
    max_windows: int = DEFAULT_MAX_WINDOWS,
) -> list[dict[str, Any]]:
    """Build non-overlapping chronological test windows.

    Each window is [train_start, split) for training and [split, test_end)
    for testing. A test window never contributes observations to its own
    training set or to an earlier window.
    """
    data = validate_rows(rows)
    n = len(data)
    train_bars = max(1, int(train_bars))
    test_bars = max(1, int(test_bars))
    step_bars = max(1, int(step_bars))
    min_train_bars = max(1, int(min_train_bars))
    max_windows = max(1, int(max_windows))

    windows = []
    start = 0
    while start + max(train_bars, min_train_bars) + test_bars <= n:
        train_end = start + train_bars
        test_end = train_end + test_bars
        train_start = start
        if train_end - train_start < min_train_bars:
            break
        windows.append({
            "index": len(windows),
            "train_start": train_start,
            "train_end": train_end,
            "test_start": train_end,
            "test_end": test_end,
            "train_start_ts": _ts(data[train_start]),
            "train_end_ts": _ts(data[train_end - 1]),
            "test_start_ts": _ts(data[test_end - 1]) if test_end == train_end + 1 else _ts(data[train_end]),
            "test_end_ts": _ts(data[test_end - 1]),
        })
        if len(windows) >= max_windows:
            break
        start += step_bars
    return windows


def evaluate_windows(
    rows: Sequence[Sequence[Any]],
    signal_fn: Callable[[int, Sequence[Sequence[Any]], Sequence[Sequence[Any]]], Any],
    outcome_fn: Callable[[int, Any, Sequence[Sequence[Any]]], dict[str, Any] | None],
    *,
    train_bars: int = DEFAULT_TRAIN_BARS,
    test_bars: int = DEFAULT_TEST_BARS,
    step_bars: int = DEFAULT_STEP_BARS,
    min_train_bars: int = DEFAULT_MIN_TRAIN_BARS,
    max_windows: int = DEFAULT_MAX_WINDOWS,
) -> dict[str, Any]:
    """Evaluate a caller-supplied signal engine without look-ahead.

    signal_fn(index, train_rows, rows) may use the full historical prefix
    ending at index, but train_rows is the only permitted source for learned
    statistics/thresholds. outcome_fn may inspect future candles only.
    """
    data = validate_rows(rows)
    windows = build_windows(
        data, train_bars, test_bars, step_bars, min_train_bars, max_windows
    )
    all_trades: list[dict[str, Any]] = []
    window_reports = []

    for window in windows:
        train = data[window["train_start"]:window["train_end"]]
        trades = []
        for i in range(window["test_start"], window["test_end"]):
            signal = signal_fn(i, train, data)
            if not signal:
                continue
            result = outcome_fn(i, signal, data)
            if not result:
                continue
            row = dict(result)
            row.setdefault("signal_index", i)
            row["walk_forward_window"] = window["index"]
            row["outcome_source"] = "walk_forward"
            trades.append(row)
            all_trades.append(row)
        closed = [t for t in trades if str(t.get("outcome", "")).upper() in ("WIN", "LOSS")]
        wins = sum(str(t.get("outcome", "")).upper() == "WIN" for t in closed)
        losses = sum(str(t.get("outcome", "")).upper() == "LOSS" for t in closed)
        window_reports.append({
            **window,
            "signals": len(trades),
            "wins": wins,
            "losses": losses,
            "expired": sum(str(t.get("outcome", "")).upper() == "EXPIRED" for t in trades),
            "win_pct": wins / len(closed) * 100.0 if closed else None,
            "trades": trades,
        })

    closed = [t for t in all_trades if str(t.get("outcome", "")).upper() in ("WIN", "LOSS")]
    wins = sum(str(t.get("outcome", "")).upper() == "WIN" for t in closed)
    losses = sum(str(t.get("outcome", "")).upper() == "LOSS" for t in closed)
    def avg(key: str):
        vals = [float(t[key]) for t in all_trades if t.get(key) is not None]
        return mean(vals) if vals else None

    milestone_rates = {}
    for pct in (5, 10, 20, 30, 50, 80):
        vals = [bool((t.get("milestones") or {}).get(str(pct))) for t in all_trades]
        milestone_rates[str(pct)] = sum(vals) / len(vals) * 100.0 if vals else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "fixed-length rolling walk-forward",
        "outcome_source": "walk_forward",
        "config": {
            "train_bars": int(train_bars),
            "test_bars": int(test_bars),
            "step_bars": int(step_bars),
            "min_train_bars": int(min_train_bars),
            "max_windows": int(max_windows),
        },
        "windows": window_reports,
        "summary": {
            "windows": len(window_reports),
            "signals": len(all_trades),
            "closed": len(closed),
            "wins": wins,
            "losses": losses,
            "expired": sum(str(t.get("outcome", "")).upper() == "EXPIRED" for t in all_trades),
            "win_pct": wins / len(closed) * 100.0 if closed else None,
            "avg_mfe_pct": avg("mfe_pct"),
            "avg_mae_pct": avg("mae_pct"),
            "milestone_rates": milestone_rates,
        },
    }


def persist(report: dict[str, Any], path: str = PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def run_walk_forward(
    rows: Sequence[Sequence[Any]],
    signal_fn: Callable[[int, Sequence[Sequence[Any]], Sequence[Sequence[Any]]], Any],
    outcome_fn: Callable[[int, Any, Sequence[Sequence[Any]]], dict[str, Any] | None],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or {}
    report = evaluate_windows(
        rows, signal_fn, outcome_fn,
        train_bars=int(cfg.get("walk_forward_train_bars", DEFAULT_TRAIN_BARS)),
        test_bars=int(cfg.get("walk_forward_test_bars", DEFAULT_TEST_BARS)),
        step_bars=int(cfg.get("walk_forward_step_bars", DEFAULT_STEP_BARS)),
        min_train_bars=int(cfg.get("walk_forward_min_train_bars", DEFAULT_MIN_TRAIN_BARS)),
        max_windows=int(cfg.get("walk_forward_max_windows", DEFAULT_MAX_WINDOWS)),
    )
    persist(report, str(cfg.get("walk_forward_stats_path", PATH)))
    return report


if __name__ == "__main__":
    print("walk_forward.py provides the chronological evaluation API; use run_walk_forward().")
