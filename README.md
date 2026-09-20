# crypto-arb-scanner

Crypto-only **SPOT** signal scanner, cross-exchange SPOT arbitrage intelligence, Telegram notifications, paper/shadow tracking, backtesting, and controlled execution infrastructure.

## Core safety policy

**SPOT ONLY.** The system does not support margin, futures, perpetuals, leverage, options, borrowing, shorting, or other derivatives.

Live execution is fail-closed and requires both the repository/runtime policy gate and environment gate. The repository baseline keeps live execution, market orders, and withdrawals disabled.

## BUY signal engine

BUY signals are evaluated across six horizons:

- **5m** — Scalp
- **15m** — Scalp
- **1h** — Medium
- **4h** — Medium
- **1d** — Long
- **1w** — Long

Each signal includes its timeframe/horizon and an estimated trade-duration window. The engine combines multi-timeframe structure, EMA/MACD/RSI/ATR, volume acceleration, entry confirmation, early-expansion classification, market regime, market quality, cross-exchange context, historical outcomes, adaptive thresholds, correlation controls, and staged target quality.

### Target potential

The configured global target-potential envelope is **5%–300%**, with additional horizon-specific ceilings:

| Horizon | Maximum target potential |
|---|---:|
| 5m | 20% |
| 15m | 35% |
| 1h | 60% |
| 4h | 100% |
| 1d | 200% |
| 1w | 300% |

These are **target-distance limits**, not guaranteed returns. Larger targets are reserved for longer horizons where the setup has more time to develop.

There is **no artificial daily BUY signal quota**. Every candidate that passes the configured quality gates can be emitted; when none qualifies, no BUY signal is sent.

The project treats an 80% precision objective as a **validation target only**, never as a guaranteed live win rate.

## Position and TP management

Signals carry their technical SL/TP1/TP2/TP3 levels into paper position tracking.

After TP milestones:

- TP1 can ratchet the protective stop upward.
- TP2 can move the runner stop further upward.
- A trailing stop never widens risk or moves downward.
- TP3 closes the tracked position.

The current paper tracker records TP milestones; it does not claim partial fills at TP1/TP2 unless an execution component explicitly performs them.

## Telegram

Telegram is used for signal notifications and controlled execution confirmation. A Telegram button does not authorize an order by itself; the backend must still validate the intent, safety policy, freshness, and execution constraints.

## Validation and development

Run unit tests with:

`python -m unittest discover -s tests -v`

Run the production baseline audit with:

`python production_readiness.py`

Profile-aware historical validation:

`python validation.py`

See `docs/PRODUCTION_OPERATIONS.md` for the controlled-live checklist, emergency stop, reconciliation, observability, and rollback procedure.

Historical/backtest statistics are descriptive evidence only. Real execution depends on exchange state, liquidity, fees, slippage, and network availability.

## Current completion path

Before any controlled live trading is considered:

1. Establish repeatable CI green status.
2. Run profile-aware out-of-sample validation with adequate closed samples.
3. Reconcile the historical validator with the live signal engine so validation measures the same entry/target logic.
4. Observe shadow/paper signals long enough to measure precision, target reach, drawdown, and execution-quality assumptions.
5. Verify every exchange adapter and SPOT-only execution guard.
6. Verify deposit/withdrawal status and network compatibility before any transfer.
7. Keep live execution and withdrawals disabled until the evidence and operational checks justify enabling them explicitly.

No step above is a profitability guarantee.


## Exact live-engine validation
The validation suite now replays the production BUY signal engine on closed historical candles across 5m, 15m, 1h, 4h, 1d and 1w. It supplies historical candles directly to the same signal path, disables adaptive live-outcome thresholds and live target optimization during replay to avoid leakage, and separates train, validation and OOS periods. The OOS report includes closed-sample gating, precision, milestone reach, average potential/R:R, MAE/MFE and holding duration. Historical results are evidence only and are not a guarantee of future performance.
